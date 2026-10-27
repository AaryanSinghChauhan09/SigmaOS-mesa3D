"""Graphics stack engine for SigmaOS-mesa3D / xorg-server
(inspired by mesa, libva-v4l2_request, xorg-server-lts).

Principles: build plans are computed, never executed (plan-before-act),
meson options are validated against the parsed option database (fail-fast on
unknown options and illegal combo values), DRI driver names come from one
authoritative table (DRY), config snippets are pure string generation.
"""
import argparse, os, re, sys

ENGINE = {
    "name": "sigma-graphics",
    "summary": "Plan mesa/xorg builds, inspect features, DRI and config",
    "language": "python",
    "verbs": ["build-plan", "features", "dri", "config"],
    "principles": ["Plan Before Act", "Fail-Fast", "Single Source of Truth",
                   "Defensive Programming", "KISS", "DRY",
                   "Deterministic Testing"],
}

cli_doc = """\
$ sigma-graphics build-plan meson_options.txt --set gallium-drivers=radeonsi,iris
meson setup build -Dgallium-drivers=radeonsi,iris -Dprefix=/usr
ninja -C build all
$ sigma-graphics features --driver radeonsi
radeonsi: opengl=4.6 vulkan=1.3 vaapi=1
$ sigma-graphics dri radeonsi nouveau
radeonsi -> lib/dri/radeonsi_dri.so
nouveau  -> lib/dri/nouveau_dri.so
$ sigma-graphics config --profile gaming
Section "Device"
    Option "AccelMethod" "glamor"
"""

_OPT_NAME_RE = re.compile(r"option\('([A-Za-z0-9_-]+)'")
_OPT_TYPE_RE = re.compile(r"type:\s*'(\w+)'")
_OPT_VALUE_RE = re.compile(
    r"value:\s*(true|false|'[^']*'|\"[^\"]*\"|\[[^\]]*\]|[0-9.]+)")
_OPT_CHOICES_RE = re.compile(r"choices:\s*\[([^\]]*)\]")
_CHOICE_RE = re.compile(r"'([^']*)'")


class GraphicsError(ValueError):
    pass


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def parse_options(text):
    """Parse meson option() lines into {name: {type, value, choices}}.

    option() calls may span several lines; continuation lines are joined
    until the parentheses balance before parsing.
    """
    options = {}
    chunks, buf, depth = [], "", 0
    for raw in text.splitlines():
        line = raw.strip()
        if not buf:
            if not line.startswith("option("):
                continue
            buf, depth = line, line.count("(") - line.count(")")
            if depth <= 0:
                chunks.append(buf)
                buf = ""
        else:
            buf += " " + line
            depth += line.count("(") - line.count(")")
            if depth <= 0:
                chunks.append(buf)
                buf = ""
    if buf:  # unterminated option() call
        raise GraphicsError(f"unterminated option() call: {buf!r}")
    for lineno, line in enumerate(chunks, 1):
        name_m = _OPT_NAME_RE.search(line)
        type_m = _OPT_TYPE_RE.search(line)
        if not name_m or not type_m:
            raise GraphicsError(f"option block {lineno}: unparsable: {line!r}")
        name, otype = name_m.group(1), type_m.group(1)
        if name in options:
            raise GraphicsError(f"option block {lineno}: duplicate option {name!r}")
        value_m = _OPT_VALUE_RE.search(line)
        if not value_m:
            raise GraphicsError(f"option block {lineno}: option {name!r} has no value")
        raw_value = value_m.group(1)
        choices_m = _OPT_CHOICES_RE.search(line)
        choices = _CHOICE_RE.findall(choices_m.group(1)) \
            if choices_m else None
        if otype == "boolean":
            value = raw_value == "true"
        elif otype == "integer":
            value = int(raw_value)
        elif otype == "array":
            value = _CHOICE_RE.findall(raw_value)
        else:  # combo / string
            value = raw_value.strip("'\"")
        if otype == "combo" and choices is None:
            raise GraphicsError(f"option block {lineno}: combo {name!r} needs choices")
        options[name] = {"type": otype, "value": value, "choices": choices}
    if not options:
        raise GraphicsError("no options found")
    return options


def apply_sets(options, sets):
    """Validate `key=value` overrides against the option database."""
    chosen = {}
    for spec in sets:
        if "=" not in spec:
            raise GraphicsError(f"--set needs KEY=VALUE, got {spec!r}")
        name, _, value = spec.partition("=")
        name = name.strip()
        if name not in options:
            raise GraphicsError(
                f"unknown option {name!r} "
                f"(known: {', '.join(sorted(options))})")
        opt = options[name]
        if opt["type"] == "combo":
            items = value.split(",")
            bad = [i for i in items if i not in opt["choices"]]
            if bad:
                raise GraphicsError(
                    f"option {name!r}: invalid value(s) {bad}; "
                    f"choices are {opt['choices']}")
            chosen[name] = ",".join(items)
        elif opt["type"] == "boolean":
            if value not in ("true", "false"):
                raise GraphicsError(f"option {name!r} is boolean; "
                                    f"got {value!r}")
            chosen[name] = value
        elif opt["type"] == "array":
            chosen[name] = _CHOICE_RE.findall(value)
        elif opt["type"] == "integer":
            if not value.isdigit():
                raise GraphicsError(f"option {name!r} is an integer; "
                                    f"got {value!r}")
            chosen[name] = int(value)
        else:
            chosen[name] = value
    return chosen


def build_plan(options, sets, builddir="build"):
    """Pure meson/ninja command plan for the requested option overrides."""
    chosen = apply_sets(options, sets)
    flags = []
    for name in sorted(chosen):
        value = chosen[name]
        if isinstance(value, list):
            value = "[" + ",".join(value) + "]"
        flags.append(f"-D{name}={value}")
    steps = ["meson setup " + builddir] + [f"  {f}" for f in flags]
    steps.append(f"ninja -C {builddir} all")
    return {"flags": flags, "steps": steps}


FEATURE_DB = """\
radeonsi  opengl=4.6  vulkan=1.3  vaapi=1   video-codecs=av1,h264,h265
nouveau   opengl=4.3  vulkan=none vaapi=0   video-codecs=h264
iris      opengl=4.6  vulkan=1.3  vaapi=1   video-codecs=av1,h264
swrast    opengl=4.5  vulkan=1.3  vaapi=0   video-codecs=none
"""


def parse_features(text):
    """Parse the driver feature table into {driver: {cap: value}}."""
    table = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        driver = parts[0]
        caps = {}
        for token in parts[1:]:
            if "=" not in token:
                raise GraphicsError(f"line {lineno}: bad capability {token!r}")
            key, _, value = token.partition("=")
            caps[key] = value
        if driver in table:
            raise GraphicsError(f"line {lineno}: duplicate driver {driver!r}")
        table[driver] = caps
    if not table:
        raise GraphicsError("empty feature table")
    return table


def driver_features(table, driver):
    if driver not in table:
        raise GraphicsError(
            f"unknown driver {driver!r} "
            f"(known: {', '.join(sorted(table))})")
    return dict(sorted(table[driver].items()))


_DRI_MAP = {"radeonsi": "radeonsi_dri.so", "nouveau": "nouveau_dri.so",
            "iris": "iris_dri.so", "i915": "i915_dri.so",
            "crocus": "crocus_dri.so", "vmwgfx": "vmwgfx_dri.so",
            "swrast": "swrast_dri.so", "virtio_gpu": "virtio_gpu_dri.so"}


def dri_plan(drivers, libdir="lib"):
    """Map requested Gallium drivers to their DRI .so install paths."""
    if not drivers:
        raise GraphicsError("no drivers requested")
    plan = []
    for driver in drivers:
        if driver not in _DRI_MAP:
            raise GraphicsError(
                f"unknown DRI driver {driver!r} "
                f"(known: {', '.join(sorted(_DRI_MAP))})")
        plan.append({"driver": driver,
                     "path": os.path.join(libdir, "dri", _DRI_MAP[driver])})
    return plan


_CONFIG_PROFILES = {
    "gaming": 'Section "Device"\n'
              '    Identifier "SigmaOS GPU"\n'
              '    Option "AccelMethod" "glamor"\n'
              '    Option "SwapbuffersWait" "true"\n'
              'EndSection\n',
    "laptop": 'Section "Device"\n'
              '    Identifier "SigmaOS GPU"\n'
              '    Option "TearFree" "true"\n'
              '    Option "DPI" "96"\n'
              'EndSection\n',
    "server": 'Section "Device"\n'
              '    Identifier "SigmaOS GPU"\n'
              '    Option "AutoAddGPU" "false"\n'
              'EndSection\n',
}


def config_snippet(profile, dpi=None):
    """Generate an xorg.conf.d snippet for a known profile."""
    if profile not in _CONFIG_PROFILES:
        raise GraphicsError(
            f"unknown profile {profile!r} "
            f"(known: {', '.join(sorted(_CONFIG_PROFILES))})")
    snippet = _CONFIG_PROFILES[profile]
    if dpi is not None:
        if not isinstance(dpi, int) or not 48 <= dpi <= 300:
            raise GraphicsError(f"dpi must be 48..300, got {dpi!r}")
        snippet += ('Section "Monitor"\n'
                    '    Identifier "SigmaOS Monitor"\n'
                    f'    Option "DPI" "{dpi} x {dpi}"\n'
                    'EndSection\n')
    return snippet


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=ENGINE["name"],
                                description=ENGINE["summary"])
    sub = p.add_subparsers(dest="cmd", required=True)
    bp = sub.add_parser("build-plan", help="plan a meson/ninja build")
    bp.add_argument("options_file")
    bp.add_argument("--set", action="append", default=[], dest="sets",
                    metavar="OPT=VALUE")
    bp.add_argument("--builddir", default="build")
    ft = sub.add_parser("features", help="show driver capabilities")
    ft.add_argument("--driver", default=None)
    ft.add_argument("--table", default=None,
                    help="feature table file (defaults to built-in)")
    dr = sub.add_parser("dri", help="map drivers to DRI .so paths")
    dr.add_argument("drivers", nargs="+")
    dr.add_argument("--libdir", default="lib")
    cf = sub.add_parser("config", help="generate an xorg snippet")
    cf.add_argument("--profile", required=True,
                    choices=sorted(_CONFIG_PROFILES))
    cf.add_argument("--dpi", type=int, default=None)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "build-plan":
            options = parse_options(_read(args.options_file))
            plan = build_plan(options, args.sets, builddir=args.builddir)
            for step in plan["steps"]:
                print(step)
        elif args.cmd == "features":
            text = _read(args.table) if args.table else FEATURE_DB
            table = parse_features(text)
            if args.driver:
                caps = driver_features(table, args.driver)
                joined = " ".join(f"{k}={v}" for k, v in caps.items())
                print(f"{args.driver}: {joined}")
            else:
                for driver in sorted(table):
                    caps = " ".join(f"{k}={v}" for k, v in
                                    sorted(table[driver].items()))
                    print(f"{driver:<10} {caps}")
        elif args.cmd == "dri":
            for item in dri_plan(args.drivers, libdir=args.libdir):
                print(f"{item['driver']:<10} -> {item['path']}")
        elif args.cmd == "config":
            print(config_snippet(args.profile, dpi=args.dpi), end="")
        return 0
    except (GraphicsError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def self_test() -> None:
    import tempfile
    options_text = """\
option('gallium-drivers', type: 'combo', value: 'radeonsi,nouveau',
       choices: ['radeonsi', 'nouveau', 'iris', 'swrast', 'all', 'none'])
option('vulkan-drivers', type: 'combo', value: 'radeonsi',
       choices: ['radeonsi', 'swrast', 'none'])
option('platforms', type: 'array', value: ['x11', 'wayland'])
option('dri3', type: 'boolean', value: true)
option('madvise', type: 'boolean', value: false)
option('valgrind', type: 'integer', value: 0)
option('shader-cache', type: 'string', value: 'local')
"""
    options = parse_options(options_text)
    assert set(options) == {"gallium-drivers", "vulkan-drivers",
                            "platforms", "dri3", "madvise", "valgrind",
                            "shader-cache"}
    assert options["gallium-drivers"]["value"] == "radeonsi,nouveau"
    assert options["gallium-drivers"]["choices"][:3] == \
        ["radeonsi", "nouveau", "iris"]
    assert options["dri3"]["value"] is True
    assert options["platforms"]["value"] == ["x11", "wayland"]
    assert options["valgrind"]["value"] == 0
    assert options["shader-cache"]["value"] == "local"

    # apply_sets validation
    chosen = apply_sets(options, ["gallium-drivers=iris,swrast",
                                  "dri3=false", "platforms=[x11]"])
    assert chosen["gallium-drivers"] == "iris,swrast"
    assert chosen["dri3"] is False or chosen["dri3"] == "false"
    for bad in ("nosuch=1", "gallium-drivers=geforce",
                "dri3=maybe", "valgrind=lots", "novalue"):
        try:
            apply_sets(options, [bad])
        except GraphicsError:
            pass
        else:
            raise AssertionError(f"--set accepted: {bad!r}")

    plan = build_plan(options, ["gallium-drivers=radeonsi,iris",
                                "dri3=false"])
    assert plan["flags"] == ["-Ddri3=false", "-Dgallium-drivers=radeonsi,iris"]
    assert plan["steps"][0] == "meson setup build"
    assert plan["steps"][-1] == "ninja -C build all"

    # option database itself is validated
    for bad_text in ("option('x', type: 'combo', value: 'a')",   # no choices
                     "option('dup', type: 'boolean', value: true)\n"
                     "option('dup', type: 'boolean', value: false)",
                     "option(42, type: 'boolean', value: true)",
                     "option('novalue', type: 'string')",
                     "# nothing\n"):
        try:
            parse_options(bad_text)
        except GraphicsError:
            pass
        else:
            raise AssertionError(f"options accepted: {bad_text!r}")

    # feature table
    table = parse_features(FEATURE_DB)
    assert set(table) == {"radeonsi", "nouveau", "iris", "swrast"}
    assert driver_features(table, "radeonsi") == \
        {"opengl": "4.6", "vaapi": "1", "video-codecs": "av1,h264,h265",
         "vulkan": "1.3"}
    try:
        driver_features(table, "geforce")
    except GraphicsError as e:
        assert "unknown driver" in str(e)
    else:
        raise AssertionError("unknown driver accepted")
    try:
        parse_features("radeonsi opengl=4.6\nradeonsi vulkan=1.3\n")
    except GraphicsError:
        pass
    else:
        raise AssertionError("duplicate driver accepted")

    # DRI mapping
    plan = dri_plan(["radeonsi", "nouveau"])
    assert [i["path"] for i in plan] == \
        ["lib/dri/radeonsi_dri.so", "lib/dri/nouveau_dri.so"]
    assert dri_plan(["swrast"], libdir="/usr/lib64")[0]["path"] == \
        "/usr/lib64/dri/swrast_dri.so"
    for bad in ([], ["geforce"], ["radeonsi", "bogus"]):
        try:
            dri_plan(bad)
        except GraphicsError:
            pass
        else:
            raise AssertionError(f"dri accepted: {bad!r}")

    # config snippets
    snippet = config_snippet("gaming")
    assert 'Option "AccelMethod" "glamor"' in snippet
    assert snippet.count('Section "') == 1
    assert snippet.rstrip().endswith("EndSection")
    withdpi = config_snippet("laptop", dpi=144)
    assert 'Option "DPI" "144 x 144"' in withdpi
    assert withdpi.count('Section "') == 2
    for bad in (lambda: config_snippet("kiosk"),
                lambda: config_snippet("laptop", dpi=20),
                lambda: config_snippet("laptop", dpi=1000)):
        try:
            bad()
        except GraphicsError:
            pass
        else:
            raise AssertionError("expected GraphicsError")

    # main() reads an options file from disk (tempdir)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "meson_options.txt")
        with open(path, "w") as fh:
            fh.write(options_text)
        assert parse_options(_read(path)) == options


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
