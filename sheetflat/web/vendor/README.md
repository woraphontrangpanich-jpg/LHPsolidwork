# Bundled libraries (so the 3D view works offline)

| File | From | Version | License |
|---|---|---|---|
| `three.module.min.js` | npm `three` `build/three.module.min.js` | 0.170.0 | MIT (`three.LICENSE`) |
| `OrbitControls.js` | npm `three` `examples/jsm/controls/OrbitControls.js` | 0.170.0 | MIT |

Package tarball checked against the npm registry integrity
`sha512-FQK+LEpYc0fBD+J8g6oSEyyNzjp+Q7Ks1C568WWaoMRLW+TkNNWmenWeGgJjV105Gd+p/2ql1ZcjYvNiPZBhuQ==`.
Only change: `OrbitControls.js` imports `./three.module.min.js` instead of the bare name `three`.
