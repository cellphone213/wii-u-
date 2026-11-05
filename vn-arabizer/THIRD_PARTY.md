# Third-party components

| Component | Used for | Licence |
|---|---|---|
| [VNTextPatch / VNTextProxy](https://github.com/arcusmaximus/VNTranslationTools) (arcusmaximus) | Reading/writing scripts of Japanese engines; making Shift-JIS games show other characters and a custom font | MIT |
| [KirikiriTools](https://github.com/arcusmaximus/KirikiriTools) – KirikiriUnencryptedArchive (arcusmaximus) | Lets KiriKiri games load the unencrypted translation patch | MIT |
| [FreeMote](https://github.com/UlyssesWu/FreeMote) (Ulysses) | PSB/E-mote `.scn` scenario support inside VNTextPatch | CC BY-NC-SA 4.0 |
| [Microsoft Detours](https://github.com/microsoft/Detours) | Function hooking inside VNTextProxy | MIT |
| [GARbro](https://github.com/morkt/GARbro) (morkt) | Archive formats ported to Python: CatSystem2 KIF, BGI/Ethornell ARC + DSC, Majiro ARC, AdvHD ARC, Artemis PFS, YU-RIS YPF, Softpal PAC (GARbro itself is not needed or bundled) | MIT |
| [siglus_static_key_tool](https://github.com/xmoezzz/siglus_static_key_tool) (xmoezzz) | Recovers a Siglus game's 16-byte key (bundled, built from source) | MPL-2.0 |
| [siglus_rs](https://github.com/xmoezzz/siglus_rs) (xmoezzz) | Scene.pck layout and keys (re-implemented in `archives/siglus.py`, also MPL-2.0) | MPL-2.0 |
| [KirikiriDescrambler](https://github.com/arcusmaximus/KirikiriTools) (arcusmaximus) | KiriKiri FE FE text descrambling (ported to Python) | MIT |
| [RPGMakerDecrypter](https://github.com/uuksu/RPGMakerDecrypter) (uuksu) | Reference for RGSSAD v1/v3; its sample archives are used in CI tests (not bundled) | MIT |
| [Noto Sans Arabic / Noto Sans](https://fonts.google.com/noto) | Bundled Arabic font | SIL OFL 1.1 |
| [NLLB-200](https://huggingface.co/facebook/nllb-200-distilled-1.3B) (Meta) | Offline translation model (downloaded by the user) | CC BY-NC 4.0 |
| CTranslate2, SentencePiece, PySide6, PyCryptodome, pefile, arabic-reshaper, python-bidi | Runtime libraries | MIT / Apache-2.0 / LGPL-3.0 / BSD |

The FreeMote and NLLB licences are non-commercial: VN Arabizer is a free, non-commercial tool.
