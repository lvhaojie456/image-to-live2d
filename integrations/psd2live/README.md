# Procedural motion extension

`ProceduralMotion.kt` targets the pinned GPL-3.0 psd2live engine used by Live2D Agent Kit. It is distributed under GPL-3.0-only. The Python runner verifies the engine commit and patched file hashes, creates a separate manifest entry point in `work/`, and adds this extension without modifying the upstream checkout.

The extension creates three child warps for left arm, right arm and skirt. It replaces the body secondary lattice's breath/lean keyforms and uses measured canvas anchors. Sleeves and hands share a parent so their cuff boundary receives the same deformation. Authored expression pixels are preserved as separate meshes; mouth components share one aperture transform rather than regenerating rectangle textures.

`BodyMotionSequence.java` is MIT. It calls the user's existing official Cubism Core and the Kit's MIT diagnostic rasterizer/validator. It samples exported `.motion3.json` values, checks all requested body extreme combinations, triangle orientation and grounded feet. The rasterizer approximates pixel rendering and does not simulate physics. No SDK binaries are included.
