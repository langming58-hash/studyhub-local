# beta.2 screenshot provenance

These are genuine native-window captures of the published StudyHub Local
`v0.3.0-beta.2` application, not mockups or generated application images.

## Source and framing

- [Published release](https://github.com/langming58-hash/studyhub-local/releases/tag/v0.3.0-beta.2)
- Release commit: `ed58d8b57ba97a30ec0ab915c6185cad85d2c092`
- Published DMG SHA-256: `ca1b4b84f479bd856ef650bb2eaabda36949366a67122325fae12ca8eaa2a634`
- The copied app passed strict code-signature integrity verification.
- Default 1280 × 820 macOS window; 2560 × 1640 native JPEG captures.
- English interface, light appearance, consistent framing. No crop, retouch,
  compositing, mock-device frame or replacement UI was applied.
- Native capture metadata contains pixel dimensions only; no GPS, author,
  personal path or account metadata.

## Isolated synthetic state

The final capture instance used a disposable process home and separate library,
settings, database, caches and logs. A process sandbox denied personal-file
contents outside the task workspace, denied Keychain service access and blocked
external network connections. The native Production capability profile was
retained for the real Export Backup control; Demo/Test capabilities and the app
binary were not modified. The normal installed app was not used for captures.

The library contains 12 fictional local materials across CS101, ECON201 and
STAT210, including an authored three-page statistics handout. Local scanner
conventions label these user-authored fixtures “My Work”. Two saved notes have
validated source-version/page anchors and three manual review cards use the
real local card model. Existing public text fixtures came from
`tests/fixtures/product-gallery/`; no institutional course materials or real
Canvas/AI account data were used. The distributed app still opens empty.

## What each image establishes

- `beta2-home-dashboard.jpg`: actual Home with fictional courses and resume UI.
- `beta2-pdf-reader.jpg`: PDF Reader V1 displaying the synthetic handout.
- `beta2-source-linked-notes-excerpts.jpg`: already-saved page-linked excerpt
  notes beside the real PDF. A usable native PDF text-selection gesture was
  not obtained through UI automation during this capture; this image is not
  evidence that the selection gesture was verified.
- `beta2-manual-fsrs-review-cards.jpg`: a real manual-card review session with
  revealed answer, source-page link and Again / Hard / Good / Easy controls.
- `beta2-local-data-export-backup.jpg`: Settings > Local Data with the native
  Export Backup control available and the beta.2 version visible.

Each saved image was visually inspected for privacy; local Apple Vision OCR
and image-metadata checks supplemented that review. These checks are not human
GUI workflow acceptance. Full real-GUI backup save/restore/undo acceptance
remains incomplete. No Restore Apply/Undo success is staged, and unmerged
PR #44 PDF Highlights are not depicted.

## Native image checksums

| File | SHA-256 |
| --- | --- |
| `beta2-home-dashboard.jpg` | `1c6ec102eee7fba327f134691bbe7d8def080c6816e72ffdd42b756c686b998b` |
| `beta2-local-data-export-backup.jpg` | `6176e64593a3e9437ecd5bed2132424052c52478671c1f0148a04510e05a5e70` |
| `beta2-manual-fsrs-review-cards.jpg` | `708a6ccea147efbf2d768627dc7c38e109f9bdbbf17e48d259024f8f2b2eb392` |
| `beta2-pdf-reader.jpg` | `17f08d0d11f0396cd6240b51046cc0067438e6d38079a241cacc70d4ed54e2f6` |
| `beta2-source-linked-notes-excerpts.jpg` | `179296625bdfe9f507fea0e2d511babfdb48c331ac5481000b680f13bec73680` |
