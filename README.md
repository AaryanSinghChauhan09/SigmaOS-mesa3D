    # SigmaOS-mesa3D

    Mesa 3D build & VA-API/V4L2 request support

    Part of the **SigmaOS** sovereign operating-system ecosystem — a multi-repository
    distribution modelled on Linux Mint and Omarchy Linux. See the parent
    [`SigmaOS`](https://github.com/AaryanSinghChauhan09/SigmaOS) repository for the
    full component index.

    ## Upstream inspirations
    - https://github.com/omacom/mesa
- https://github.com/omacom/libva-v4l2_request

    ## Design principles applied
    - **Plan Before Act**
- **Fail-Fast**
- **Single Source of Truth**
- **Defensive Programming**
- **KISS**
- **DRY**
- **Deterministic Testing**

    ## Usage
    Verbs: `build-plan, features, dri, config`

    ```
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

    ```

    ## Build & test
    ```
    make build
    make test
    ```

    ## Licence
    GNU General Public License v3.0 (FOSS).
