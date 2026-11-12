# Desktop Python dependency locks

Status: **IMPLEMENTED IN THIS PR, pending independent review**.
The existing public beta.2 Release and binary are unchanged.

The packaged desktop target is CPython 3.13.15, macOS arm64, with a macOS 13.0
minimum wheel target. Dependency tooling is uv 0.12.3. No Python project or
resolver migration is required: the existing direct-intent files remain
[requirements.txt](../requirements.txt) and
[requirements-desktop.txt](../requirements-desktop.txt).

## Runtime and build graphs

| Lock | Scope |
| --- | --- |
| [runtime-macos-arm64-py313.txt](../requirements/locks/runtime-macos-arm64-py313.txt) | 41 exact, hashed runtime distributions: FSRS and the selected MarkItDown DOCX/PDF/PPTX/XLSX converter graph |
| [desktop-macos-arm64-py313.txt](../requirements/locks/desktop-macos-arm64-py313.txt) | The same 41 runtime versions plus five build-only distributions: PyInstaller, its hooks, altgraph, macholib and setuptools |
| [desktop-macos-arm64-py313.json](../requirements/locks/desktop-macos-arm64-py313.json) | Target/tool identity and SHA-256 fingerprints binding both intent files and both locks |

MarkItDown depends on Magika and its ONNX inference runtime. Its requested
extras bring document libraries such as mammoth/lxml, PDF parsers, Pillow,
pypdfium2, python-pptx, openpyxl and pandas. PyInstaller is used to freeze the
backend and converter helper; it is not an end-user installation requirement.
The existing helper separation and converter/network/credential boundaries
are unchanged.

## The necessary compatibility and privacy pins

The previously installed ONNX Runtime 1.31.1 CPython 3.13 arm64 wheel is tagged
macosx_14_0_arm64. This is a mismatch with the declared macOS 13 floor.
[PyPI's 1.31.1 file inventory](https://pypi.org/project/onnxruntime/1.31.1/#files)
and the installed wheel metadata both establish that minimum.

The locked graph uses ONNX Runtime 1.23.2, whose
[CPython 3.13 arm64 wheel](https://pypi.org/project/onnxruntime/1.23.2/#files)
is tagged macosx_13_0_arm64 and satisfies Magika 1.0.3's Python 3.13
requirement of ONNX Runtime >=1.21.0. NumPy is pinned to 2.4.1 for the native
wheel privacy reason documented below. The other 40 existing pins are retained,
including FSRS 6.3.2, MarkItDown
0.1.9, PyInstaller 6.22.2, certifi 2026.10.23 and the converter libraries.
The selected ONNX version requires four additional transitives:
coloredlogs 15.0.1, humanfriendly 10.0, sympy 1.14.0 and mpmath 1.3.0.

This pin addresses platform compatibility. pip-audit found no known
vulnerabilities in the resulting 46-distribution environment. Wheel tags
describe compatibility metadata; they do not verify native execution on every
supported machine. A real macOS 13 clean-machine run remains pending.

The official NumPy 2.5.4 macOS 11 arm64 wheel contains a GNU runtime library
with 37 macOS-home-path matches. The macOS 13 target selects that wheel rather
than the different macOS 14 wheel. Freezing preserves those strings in two
packaged copies. NumPy 2.4.1 is the nearest tested earlier release whose
compatible wheel's 20 native files pass the unchanged privacy patterns; all
tested 2.4.2–2.4.6 and 2.5.0–2.5.4 compatible wheels retain the offending library.
See the [sanitized provenance investigation](audits/numpy-wheel-privacy-2026-11-13.md)
for artifact identities, limitations and the reasons library removal was rejected.

## Installation and drift rejection

The existing desktop setup command installs the pinned managed interpreter,
then uses uv pip sync with required SHA-256 hashes, binary wheels only,
strict dependency checking, and the explicit macOS arm64/Python target.
MACOSX_DEPLOYMENT_TARGET is scoped to 13.0 for dependency operations so the
builder's newer OS does not choose a higher-minimum wheel.

Before changing the generated environment, setup verifies the intent/lock
fingerprints, supported host and uv version. After sync, and again before
PyInstaller runs, verification checks:

- exact CPython version and supported macOS arm64 host;
- every installed distribution's exact version, with no missing, duplicate
  or extra distributions;
- every installed wheel's tags against CPython 3.13/macOS 13 arm64 using the
  locked packaging library's standard compatibility rules;
- NumPy's installed native files against the existing artifact scanner's
  unchanged patterns, before freezing. Failures expose category and filename
  only. Missing native inventory also fails closed.

An edited intent file, modified lock, unpinned requirement, missing hash,
runtime/build divergence or wrong environment fails closed. No source
distribution build is allowed, so unpinned build-isolation dependencies
cannot enter this install path. uv's generated hash lists can contain
multiple distribution artifacts for a pinned version; platform and
binary-only selection still restrict what may be installed.

The macOS workflow prepares this environment before normal CI and puts its
bin directory on GITHUB_PATH. Normal tests, backend freezing and packaged
acceptance therefore consume the same locked Python environment. Lock-only
changes trigger macOS CI. The Linux Python 3.12 source-CI install and general
source-development installs remain outside these macOS-specific locks.

## Regeneration and review

1. Use the pinned uv version and run the existing desktop setup on macOS arm64.
2. Edit direct requirements only when an intentional dependency change is
   needed. Do not run a broad upgrade.
3. Run python3 bin/desktop_dependencies.py --generate. Existing locked
   versions are used as constraints. For a necessary version change, prepare
   an explicit reviewed constraints file and pass --constraints with
   --generate. The generator uses the project interpreter, explicit
   platform/minimum target, binary-only resolution and SHA-256 generation.
4. Review both generated locks, their binding manifest and all graph changes.
   Runtime resolution is constrained by the build graph so shared versions
   cannot diverge.
5. Run setup, the lock acceptance tests, normal CI and packaged acceptance.
   Run a fresh advisory audit. Submit the changes for independent review.

The generator delegates dependency resolution and hash generation to uv;
the verifier accepts only this project's closed exact-pin/hash format.
Fingerprints detect drift, not an authorized reviewer changing all related
files together. Regeneration is explicit; CI does not rewrite locks or
accept a newer resolver result automatically. A repeated generation with
unchanged inputs and pins was byte-identical during validation.

See [uv's locking documentation](https://docs.astral.sh/uv/pip/compile/) and
[platform targeting reference](https://docs.astral.sh/uv/reference/cli/).

## Verification boundaries

Offline regression checks run in normal CI. The macOS workflow additionally
performs a real uv install with an intentionally wrong artifact hash and
requires rejection for that reason. Wheel-tag tests reject macOS-14-only,
Intel-only and wrong-Python wheels for this target. A separate runtime-only
environment is validated without the five builder distributions.

The [five non-macOS Rust warnings](audits/dependency-supply-chain-2026-11-12.md)
remain documented and unchanged. There is no Rust/Tauri migration, application
version bump, tag or Release change in this work. No paid software, service,
certificate or subscription is required.

These checks are not full human GUI backup save/restore/undo acceptance.
That acceptance remains incomplete. Hash locking is not a binary SBOM,
malware proof, or a byte-identical build guarantee across compiler/SDK changes.
