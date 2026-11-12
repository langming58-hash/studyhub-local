# NumPy desktop wheel privacy investigation

Status: **IMPLEMENTED IN PR #50, pending independent review**. Historical
failure evidence is bound to blocked HEAD
`e0df0bbc3f2b44366b35a1a9f4f24fe4fb2a4cea`. The public beta.2 binary is unchanged.

## Proven cause

A clean locked setup and fresh app/DMG build at the blocked HEAD reproduced
exactly two macOS-home-path findings in the unchanged app scanner (216 files).
Both are copies of `libgcc_s.1.1.dylib`: one at `backend/_internal/`, the other
at `backend/_internal/numpy/.dylibs/`.

The installed NumPy WHEEL tag is `cp313-cp313-macosx_11_0_arm64`. All 926
hashed RECORD payloads and lengths match the official NumPy 2.5.4 macOS 11
wheel, with zero mismatches. This establishes the installed artifact identity,
rather than inferring it from the package version or a resolver log.

| Official NumPy 2.5.4 CPython 3.13 arm64 artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `numpy-2.5.4-cp313-cp313-macosx_11_0_arm64.whl` | 12009826 | `7415db95818b39ec475a5eea54d9e3b6bc83e3912158e46da3438cdce399804d` |
| `numpy-2.5.4-cp313-cp313-macosx_14_0_arm64.whl` (comparison only) | 5445803 | `6d6a71b9d9a97c03633aa12565ef2825ffa036cc1d99cfd50dacf0f128af4fe2` |

The macOS 14 wheel has no `numpy/.dylibs/` payload. It is a different artifact,
not an acceptable substitute for the supported macOS 13 target. Both hashes
were present in the blocked lock. Explicit macOS 13 targeting changes which
wheel can be selected while retaining the same NumPy version.

| libgcc payload | Bytes | Architecture | Home-path matches | SHA-256 |
| --- | ---: | --- | ---: | --- |
| Upstream wheel and installed RECORD entry | 158976 | arm64 | 37 | `baee2e6118dd54c0852f881d718516d70884de84bfaa085de5d8d8f90baa3b06` |
| Packaged root copy | 142592 | arm64 | 37 | `adb08135499a63e79fc49021e5e0613c88f66e5475941ce41bbe8dcc83d3dc88` |
| Packaged NumPy copy | 142592 | arm64 | 37 | `adb08135499a63e79fc49021e5e0613c88f66e5475941ce41bbe8dcc83d3dc88` |

Both packaged copies are distinct regular files with identical contents.
Every full matched NUL-terminated string in both copies already exists in
the upstream wheel. Freezing changes binary hashes/size and offsets; it does
not introduce these strings. Upstream offsets range from 132167 to 139500;
packaged offsets range from 115783 to 123116. The
[structured evidence](numpy-wheel-privacy-2026-11-13.json) records every offset,
string digest, sanitized dependency category, RECORD entry and artifact identity.
No matched path values are retained in the evidence.

## Runtime collection decision

The upstream OpenBLAS library depends on libgfortran. Both packaged
`libgfortran.5.dylib` copies have an incoming runtime load reference to
`@rpath/libgcc_s.1.1.dylib`, and each directory contains its sibling libgcc.
libgcc itself links to the system `libSystem.B.dylib`; its own install ID is
recorded separately from external dependencies in the evidence.

This establishes a real dependency chain. It does not establish that both
physical copies are independently needed under every loader search path.
Neither copy has been proven safely removable. A collection exclusion is
therefore unjustified, and no PyInstaller collection rule or binary was altered.

## Minimal compatible change

Official compatible arm64 wheels for NumPy 2.5.0–2.5.4 and 2.4.2–2.4.6 all
contain the same offending libgcc payload. NumPy 2.4.1 is the closest earlier
release tested with zero pattern matches in all 20 native files. Its official
wheel is `numpy-2.4.1-cp313-cp313-macosx_11_0_arm64.whl`, 12368657 bytes,
SHA-256 `82c55962006156aeef1629b953fd359064aa47e4d82cfc8e67f0918f7da3344f`.
All 910 installed RECORD payloads match that archive. Each native file is arm64
and its Mach-O deployment minimum is at most macOS 13.

Only NumPy changes from the blocked 46-distribution graph, from 2.5.4 to 2.4.1.
Both hash locks and their binding manifest are regenerated with the pinned uv
resolver and explicit constraints retaining every other version. Root intent
requirements, application version, permissions and PyInstaller collection
remain unchanged. Upstream wheel contents and license notices are preserved.

The environment verifier now fails before freezing when any installed NumPy
native file matches the existing scanner patterns. Regression checks cover
clean payloads, a synthetic home-path category, and missing inventory. The
packaged acceptance suite additionally converts a synthetic XLSX through the
actual frozen MarkItDown/pandas/NumPy helper with a restricted PATH and an
isolated HOME, checking output and source immutability. Existing checks remain.

Sources: [official 2.5.4 file inventory](https://pypi.org/project/numpy/2.5.4/#files),
[official 2.4.1 file inventory](https://pypi.org/project/numpy/2.4.1/#files).
Downloads were verified against PyPI's published SHA-256 and size.

## Verification boundaries

The artifact scanner file remains byte-identical, SHA-256
`1681cd6b8db49f0d5c8bfc869c6c20be173076afe523c28314c83a9664ea7230`.
No scan patterns, exceptions, permission boundaries or acceptance checks are
relaxed. No native bytes are patched or removed, and no macOS-14-only wheel
is selected. Exact-HEAD CI and packaging results belong to the PR checks.

Local automated verification passed: exact/hash-locked setup and deliberately
wrong-hash rejection; build and runtime inventories/imports; NumPy
BLAS/LAPACK/FFT; FSRS and real Magika/ONNX inference; real DOCX/PDF/PPTX/XLSX
conversion; full normal CI; locked Rust checks and tests (81 passed, one existing
ignored test); app/DMG build; packaged acceptance; unchanged app and mounted-DMG
privacy scans (210 files each); strict bundle signatures, DMG integrity and
SHA-256 agreement. Repeated lock generation was byte-identical. A fresh
pip-audit 2.10.1 audit covered all 46 build distributions with zero known
vulnerabilities and zero skips. These results do not guarantee absence of
undisclosed vulnerabilities or substitute for independent review.

This is a native-payload fix, not a claim that the entire upstream wheel has
no pattern matches. The 2.4.1 wheel still contains upstream home-path patterns
in Python build-configuration metadata and documentation examples, plus a
token-shaped typing identifier. Their sanitized file/category counts are
disclosed in the structured evidence. The existing artifact scan is a byte
scan of bundle files; it does not recursively decode compressed Python archives.
The native preflight does not change that scanner's scope.

Execution is tested on macOS 15 arm64 locally and in existing macOS CI. Wheel
tags and Mach-O minima are compatibility evidence, not a real macOS 13 machine
test; that clean-machine validation remains pending. There is no notarization
or Apple Developer membership purchase. The five previously documented
[non-macOS Rust warnings](dependency-supply-chain-2026-11-12.md) remain unchanged.
Full human GUI backup Export → Preview → Apply → Undo acceptance is incomplete.
No tag, Release, public DMG or checksum changes are part of this work.
