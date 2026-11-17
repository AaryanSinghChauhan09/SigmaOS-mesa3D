// Integration test for SigmaOS-mesa3D
// Verifies the crate builds and links as a dependency.
#![allow(unused_imports)]

use sigmaos_mesa3d as _;

#[test]
fn crate_links_and_runs() {
    assert_eq!(2 + 2, 4);
}
