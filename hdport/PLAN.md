# HDPort — plan for running Wii games natively on Wii U in 1080p with HD texture packs

Status: planning. Nothing here is implemented yet.

## 1. Goal

Take a Wii game the user dumped from their own disc. Optionally take a community
HD texture pack made for Dolphin. Produce a native Wii U homebrew app (`.wuhb`,
launched through Aroma) that:

- runs the game's own code on the Wii U processor, with no CPU emulation,
- draws the game with the Wii U GPU at 1920×1080,
- swaps in HD textures from Dolphin-format packs,
- uses Wii U controllers (Wii Remotes, GamePad, Pro Controller).

The repo and any packaged output contain no Nintendo code or game data. Users
supply their own dumps and run the build on their own machine.

## 2. Why the normal route can't do this, and why this route can

| | vWii (Wii mode on Wii U) | HDPort (native Wii U mode) |
|---|---|---|
| Memory available | ~88 MB (Wii limits) | ~800 MB+ for an app |
| Output resolution | 480p, upscaled by scaler | real 1080p render targets |
| Texture memory | 1 MB TMEM, Wii-sized textures | HD textures fit in MEM2 |
| GPU API | Hollywood compatibility mode | GX2 (full Latte GPU) |

The key fact that makes this possible: the Wii U CPU (Espresso) is a newer
version of the Wii CPU (Broadway). It uses the same PowerPC instruction set,
including the paired-single floating-point instructions Wii games use heavily,
so game code can run as real machine code. What has to be replaced is
everything around the CPU:

1. **Memory layout.** Wii games expect RAM at fixed addresses (`0x80000000`
   MEM1, `0x90000000` MEM2, plus uncached mirrors at `0xC0000000`/`0xD0000000`).
2. **Hardware registers.** Games write directly to Wii hardware: the graphics
   FIFO write-gather pipe, video interface, DSP, controllers and the IOS
   messaging registers.
3. **Graphics.** Wii GX commands (BP/CP/XF registers, TEV combiners) have to be
   translated to Wii U GX2 calls and shaders.
4. **System services.** IOS (disc, files, saves, Bluetooth, USB) has to be
   high-level emulated (HLE) on top of Cafe OS.
5. **Audio.** The DSP audio microcode (AX) has to be HLE'd to Wii U `sndcore2`.

## 3. Architecture

```
 PC side (hdport CLI)                         Wii U side (runtime)
 ─────────────────────                        ─────────────────────
 user's ISO/WBFS/RVZ                          game.wuhb (Aroma)
   │                                            │
   ├─ disc reader ─► DOL + RELs + files ──┐     ├─ recompiled game code
   ├─ analyzer (functions, jump tables,   │     ├─ libhdrt:
   │   SDK signature matching)            │     │   memory arena + MMIO dispatch
   ├─ recompiler (PPC ─► C, or patch      │     │   OS HLE (threads, alarms, interrupts)
   │   plan for direct execution)         │     │   IOS HLE (DI, FS, ES, STM, BT)
   ├─ texture pack converter              │     │   GX ─► GX2 translator @1080p
   │   (Dolphin PNG ─► BCn by hash)       │     │   texture replacement cache
   └─ packager (wut/devkitPPC ─► .wuhb) ◄─┘     │   AX audio HLE ─► sndcore2
                                                │   input: padscore/VPAD ─► WPAD HLE
                                                └─ SD card: disc files, textures, saves
```

### 3.1 How game code runs: decided in Phase 0

**Option A: direct execution with memory remapping (fastest).**
Use the kernel access Aroma gives homebrew to map a block of physical memory
at the virtual addresses Wii games expect. Run the original code nearly
unchanged, patching only accesses to hardware registers and SDK entry points.
Risk: Cafe OS may already use those virtual ranges, or may not let us change
the mapping. Phase 0 must prove this works or rule it out.

**Option B: static recompilation to C (safer, works anywhere).**
Translate every function in the DOL/RELs to C. Memory accesses become
`mem_base + (addr & mask)` and hardware-register accesses become handler calls.
Then compile the C for Wii U with devkitPPC. This is the same model as
N64Recomp and XenonRecomp. It costs some speed (the Wii U CPU runs at 1.24 GHz
against the Wii's 729 MHz, so there is headroom, but not much). A small PowerPC
interpreter catches indirect jumps the analyzer missed.

Recommendation: prototype both in Phase 0 on one small function set, measure
them, then commit to one. Expect Option B unless A is clearly viable.

### 3.2 Graphics: GX → GX2 at 1080p

- **FIFO decoder.** Games stream graphics commands as stores to the
  write-gather pipe address. Capture those bytes into a ring buffer and decode
  them on a separate CPU core (Espresso has 3 cores).
- **State translation.** Map BP/CP/XF register state to GX2: blend, depth,
  cull, viewport and scissor scaled to 1080p, vertex formats.
- **TEV → shaders.** Generate pixel and vertex shaders from TEV/XF state, using
  Dolphin's shader generator (GPLv2+) as the reference. Compile at runtime with
  CafeGLSL (a GLSL compiler for Wii U homebrew), cache compiled shaders on SD,
  and precompile shaders seen in earlier sessions.
- **Textures.** Decode GX formats (I4, I8, IA4, IA8, RGB565, RGB5A3, RGBA8,
  CI4/CI8/CI14 + TLUT, CMPR). Upload CMPR as BC1, which GX2 supports directly,
  and convert the others on load.
- **EFB at 1080p.** The game's framebuffer becomes a 1920×1080 GX2 render
  target. EFB-to-texture copies stay on the GPU at high resolution. CPU EFB
  reads (peek) are slow and need a per-game fallback.
- **Widescreen.** Use the game's own 16:9 mode where it has one. An optional
  per-game patch can widen the projection.

### 3.3 HD texture replacement (Dolphin pack compatibility)

- Dolphin names custom textures `tex1_{w}x{h}_{hash}[_{tluthash}]_{fmt}.png`,
  using an XXH64 hash of the raw texture data. When the runtime decodes a
  texture, it computes the same hash and looks it up in the pack index.
- PNG decoding on the Wii U is too slow, so the PC-side **pack converter**
  turns PNGs into mipmapped BC1/BC3 (or RGBA8 when needed), plus an index file
  of hash → offset and size.
- At runtime, an LRU texture cache holds what fits in a fixed memory budget
  (configurable, ~400 MB default) and streams the rest from SD/USB on demand.
  On a cache miss it shows the original texture for a frame rather than stalling.

### 3.4 System, audio, input

- **OS HLE.** Recognize Revolution SDK functions by signature (built from
  analysis of the user's own games, not from Nintendo SDK files). Replace
  threads, mutexes, alarms and timers with Cafe OS equivalents. Run interrupts
  (VI retrace, DSP, DI) at safe points.
- **IOS HLE.** DI disc reads come from extracted files on SD/USB. FS/NAND saves
  go to `sd:/hdport/<titleid>/save`. Also handle ES (title info) and STM
  (power/reset). Dolphin's IOS HLE is the behavioral reference.
- **Audio.** HLE the AX microcode (voice mixing, ADPCM decoding) and output
  through Wii U AX/`sndcore2`.
- **Input.** HLE the game's WPAD/KPAD layer and feed it data from Wii U
  `padscore` (real Wii Remotes and extensions) and VPAD (GamePad), with mapping
  profiles. GameCube controller (SI) input maps to the GamePad or Pro
  Controller.
- **Timing.** The Wii U CPU is faster, so pace frames to VI retrace (60/50 Hz)
  so games don't run too fast.

## 4. Phases and milestones

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Feasibility** (≈1 month) | Spike repo: Aroma "hello" app; Option A mapping test; Option B recompile of ~50 functions; CafeGLSL shader compile; FIFO capture | Decision memo: A or B, measured speed |
| **1. PC pipeline** | `hdport` CLI: disc reader (ISO/WBFS/RVZ), DOL/REL loader + relocations, function and jump-table analysis, SDK signature matcher | Full function map of the target game with <1% unresolved branches |
| **2. Runtime core** | `libhdrt`: memory arena, MMIO dispatch, OS HLE, DI/FS HLE | Target game boots to its first GX draw, logged to SD |
| **3. Graphics** | GX→GX2 translator, TEV shader generator, texture decoders, 1080p EFB | Title screen renders correctly; compared against Dolphin frame dumps |
| **4. Audio + input** | AX HLE, WPAD/VPAD/SI mapping | Playable start to finish with sound |
| **5. HD textures** | Pack converter + runtime cache | Community Dolphin pack loads; memory and stutter within budget |
| **6. Second game + hardening** | Per-game config format, compatibility list, crash reporter | Two games playable; known-issues list |

**First target:** pick one game that is single-threaded-ish, uses few RELs and
has an existing Dolphin HD pack. Avoid games that rely heavily on EFB reads or
exact CPU timing.

**Effort, honestly:** Phase 0 is a month for one experienced developer. Getting
the first game playable is roughly 6–12 months. Broad compatibility is a
multi-year, multi-person effort, because each game needs its own analysis,
config and testing. The pipeline makes each new game cheaper, but it will not
be one-click.

## 5. Repository layout (proposed)

```
hdport/
  PLAN.md                 this file
  tools/hdport/           PC CLI (Python or Rust): disc, analyze, recomp, pack, package
  runtime/                libhdrt (C/C++, devkitPPC + wut)
    mem/  os/  ios/  gx/  audio/  input/
  shaders/                TEV shader generator
  games/<id>/config.toml  per-game hints (entry points, patches, widescreen)
  tests/                  golden-frame comparisons, unit tests for decoders/hashing
```

## 6. Toolchain

- devkitPro: devkitPPC, `wut` (Wii U toolchain), `wuhbtool`
- Aroma environment on the user's Wii U (homebrew loader, kernel access)
- CafeGLSL for runtime shader compilation on Wii U
- Ghidra (Gekko/Broadway processor support) for manual analysis; the installed
  `reverse-engineer-anything` skill can drive it
- Dolphin as the reference: GX/TEV behavior, texture hashing, frame dumps
  for comparison

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Option A memory mapping blocked by Cafe OS | Lose the fast path | Option B fallback; decide in Phase 0 |
| Recompiled code too slow | Low frame rate | GX decode on a second core; optimize hot memory accesses; fall back to A for hot paths |
| Indirect calls / runtime-loaded code missed | Crashes | Interpreter fallback + logging of misses to feed back into analysis |
| EFB reads, CPU–GPU sync quirks | Broken effects | Per-game hacks, as Dolphin does |
| Texture hash mismatch with Dolphin packs | Packs don't load | Test hashing against Dolphin's own dumped textures |
| Shader compile stutter | Hitches | Persistent shader cache + precompile from previous runs |
| Legal | — | Ship only original code; users provide dumps they own; never distribute built games or Nintendo files |

## 8. Alternative that works today

Run Dolphin with the HD pack on a PC and stream it to the Wii U with Sunshine +
Moonlight for Wii U. This doesn't run natively, but it gives the 1080p HD
texture experience on the Wii U and TV today, and it can serve as a visual
reference while HDPort is built.

## 9. Immediate next steps

1. Choose the first target game (one the user owns, with an existing Dolphin HD pack).
2. Set up devkitPro + wut and build an Aroma hello-world `.wuhb`.
3. Phase 0 spikes: memory-mapping test (Option A), mini recompiler (Option B),
   CafeGLSL compile test, FIFO capture.
4. Write the decision memo and start Phase 1.
