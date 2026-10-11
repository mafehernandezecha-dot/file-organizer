# Smart File Organization Agent

A local-first, safety-first file organizer for macOS, built for the
Memobrick business and personal files.

> **Status: audit + first cleanup (order files).**
> `audit`, `report`, and `orders` only *look*: they write an index, reports,
> and previews inside `~/.file-organizer/` (readable only by your account).
> The only command that changes files is `apply`, and only for a plan you
> reviewed, after a Time Machine check and typing `MOVE` or `TRASH`. It moves
> files or sends them to the Trash (never permanent deletion), and `undo`
> puts them back.

## What the audit tells you

- Total files and storage, by folder and by file type
- Largest folders and largest files
- **Exact duplicates** (byte-for-byte, via SHA-256) and how much space the extra copies use
- **Version families**: `Proposal_FINAL.docx`, `Proposal v2.docx`, `Proposal (1).docx` …
- Memobrick-related files and where they're scattered
- Files untouched for over a year (for awareness only; old ≠ disposable)
- Installers and archives piling up in Downloads, screenshots on the Desktop,
  photos/videos outside Pictures and Movies
- Software projects, Photos libraries, and video-editing projects it found
  and is deliberately keeping intact

## Safety rules built in

| Rule | How |
|---|---|
| Only approved folders | You list them in `config.json`; nothing else is visited |
| No hidden/system/app data | Skips `.*`, `Library`, `node_modules`, iCloud internals, etc. |
| No credentials | Folders/files named like passwords, keys, `.env`, `.pem`, keychains are never opened |
| iCloud-only files stay in iCloud | "Optimize Storage" placeholders are indexed but never read (no surprise downloads) |
| Libraries & projects stay whole | `.photoslibrary`, `.fcpbundle`, `.prproj`, Keynote/Pages, apps, and any folder with `.git`/`package.json`/Shopify theme files are one unit |
| Symlinks/aliases not followed | Recorded only |
| No drive-crossing | Each folder stays on its own volume |
| Private output | Index and reports are `chmod 600` in `~/.file-organizer/` |
| Nothing leaves your Mac | No network calls, no AI uploads |
| No installs | Python standard library only |

## Running it on your Mac

1. **Python 3** — open Terminal and run `python3 --version`. If macOS offers
   to install the *Command Line Developer Tools*, accept (Apple's own
   installer, no third-party software). Python 3.9 or newer works.

2. **Get the code**

   ```bash
   cd ~
   git clone https://github.com/mafehernandezecha-dot/file-organizer.git
   cd file-organizer
   ```

3. **Choose approved folders**

   ```bash
   cp config.example.json config.json
   open -e config.json
   ```

   Edit `approved_folders`. Add Memobrick folders, external drives
   (e.g. `"/Volumes/MyDrive/Memobrick"`), or remove anything you don't want scanned.
   Folders you add to `excluded_folders` are skipped.

4. **Run the audit**

   ```bash
   python3 -m organizer audit --open
   ```

   The report opens in your browser. Add `--no-hash` for a faster first pass
   without duplicate checking.

   macOS may ask *"Terminal would like to access files in your Documents
   folder"* (and Desktop/Downloads). Click **Allow** for each folder you
   approved. Full Disk Access is **not** needed and should not be granted.
   Anything you deny is listed under "Skipped during scan".

5. **Re-open the last report** any time: `python3 -m organizer report --open`

## What isn't organized yet?

```bash
python3 -m organizer leftovers --open
```

Read-only. Lists (1) items left alone during the last approval and why (e.g.
iCloud-only — right-click → Download Now; changed since the scan; name clash),
(2) items kept in place on purpose (private-looking files, apps, code
folders), and (3) everything still sitting in Downloads, Desktop and loose in
Documents.

## The full folder structure

```bash
python3 -m organizer folders      # creates every folder from the plan (type CREATE)
```

Creates all sections and subfolders of `01 MEMOBRICK BUSINESS` (A–J, e.g.
D Marketing → Facebook Ads, Instagram Ads, TikTok Ads, Google Ads, Email
Marketing, Campaigns, Holiday Campaigns, Discount Promotions, Strategy &
Reports), `02 PERSONAL DOCUMENTS` and `05 INTAKE`, even if they start empty.
Only empty folders are created; `undo` removes them again while still empty.

Where marketing material goes: ad **images** → F Creative Assets/Ad Creatives,
ad **videos** → F Creative Assets/Videos, marketing **documents** → the
matching D Marketing subfolder.

## All Memobrick videos in one folder

Every Memobrick video goes to `01 MEMOBRICK BUSINESS/F Creative Assets/Videos`
— never mixed with images in Ad Creatives or Social Media. `sort` does this
for new files; for videos filed earlier:

```bash
python3 -m organizer videos --open     # preview gathering them (incl. old Videos/<year> folders)
python3 -m organizer apply ~/.file-organizer/plans/move-videos_….json      # type MOVE
```

Subfolders you create inside Videos are left as they are; videos inside code
folders stay put; non-Memobrick videos go to `05 INTAKE/Videos`. The weekly
check includes this step too.

## Red tag on Memobrick images

```bash
python3 -m organizer tag          # adds the Red Finder tag to every image in 01 MEMOBRICK BUSINESS
```

Shows the count, asks you to type `TAG`, and only adds the tag (existing tags
are kept; contents, names and dates don't change). Images inside code folders
and iCloud-only files are skipped. From then on, images that `sort`/`approve`
file into the business folder get the tag automatically
(`memobrick_image_tag` in config; `""` turns it off). `undo` removes only the
tags a run added. Click **Red** under Tags in the Finder sidebar to see them all.

## Filing everything from Downloads, Desktop and Documents

`sort` now finds a home for every item, not just Memobrick files:

| Item | Goes to |
|---|---|
| Memobrick files | `Documents/01 MEMOBRICK BUSINESS/<A–I section>` |
| Personal documents (passport, visas, travel, medical, bank/tax, lease, insurance, Hebrew studies, warranties, CV…) | `Documents/02 PERSONAL DOCUMENTS/<section>` — recognized by name or text read on this Mac only |
| Everything else | `Documents/05 INTAKE/` → Documents to Review, Photos, Videos, Audio, Compressed Files, Installers, Memobrick to Sort, Folders to Review, Needs Manual Review |

Loose files directly inside Documents are included; your existing folders in
Documents are never touched. Private-looking files, apps, and non-Memobrick
code folders always stay where they are.

## Filing Memobrick items from Downloads and Desktop

```bash
python3 -m organizer audit             # refresh the index
python3 -m organizer sort --open       # preview (changes nothing)
python3 -m organizer apply ~/.file-organizer/plans/sort-memobrick_….json   # type MOVE
python3 -m organizer undo  ~/.file-organizer/journal/….jsonl               # if needed
```

Items go into `~/Documents/01 MEMOBRICK BUSINESS/<A–I section>/…`, creating
only the folders that are needed. Each item gets a reason and a confidence:

- **High**: a clear clue: customer ID (`S-6SUKU6NGJR-photo.jpg` → Customer
  Photos/S-6SUKU6NGJR), Shopify export, logo, invoice, instructions, mockup,
  AI tool name, ad/campaign words — or the document's own text (PDF, Word,
  Excel, PowerPoint, CSV are peeked locally, first few thousand characters).
- **Check**: a reasonable guess, e.g. loose photos → Creative Assets/Photos/<year>.
- **Need your decision**: anything unclear, private-looking, installers,
  archives, and unknown software folders stay where they are.

**Looking inside pictures.** On a Mac, `sort` also uses Apple's on-device
Vision framework (no installs, nothing uploaded) to see what each loose image
shows: the Memobrick name or a transparent few-color graphic → Logos &
Graphics; promotional text → Ad Creatives; a small brick-color palette →
Mosaic Designs; lots of text → Graphics & Flyers; toys/bricks/frames →
Product Photos; faces → Photos with People (faces are counted, never
identified). Clear name rules still win. The helper is compiled once with the
Command Line Tools; results are cached. Use `--no-vision` to skip it.

Top-level folders in Downloads (an unzipped editor build, a photo shoot)
move as one piece, so nothing inside them is scattered.

## Weekly tidy-up (Review Mode)

```bash
python3 -m organizer schedule --on            # every Monday 09:00 (--day / --time to change)
python3 -m organizer schedule                 # is it on?
python3 -m organizer schedule --off           # turn it off
python3 -m organizer weekly --open            # run the check now
python3 -m organizer approve                  # carry out everything it prepared
```

The weekly check scans (read-only), prepares every preview (duplicates,
installers/archives, screenshots, shipping labels, Memobrick files), writes
one weekly report, and shows a notification. **It never changes files by
itself.** `approve` carries out all prepared plans after one Time Machine
check and typing `APPROVE`, and prints an undo command per step. To approve
just one, use the `apply` command on its preview (with `--skip` if needed).

The schedule is a per-user LaunchAgent
(`~/Library/LaunchAgents/com.memobrick.file-organizer.weekly.plist`) that
opens `~/.file-organizer/weekly.command` in Terminal, so it uses the folder
access Terminal already has: no Full Disk Access, no hidden background process.

## Installers & archives (keeping the newest theme versions)

```bash
python3 -m organizer audit
python3 -m organizer cleanup --open          # preview; --keep 5 is the default
python3 -m organizer apply ~/.file-organizer/plans/cleanup-installers-archives_….json   # type TRASH
```

Shopify theme archives and Memobrick editor/studio build archives are
grouped into families; the newest 5 (by modified date) of each are kept
together in `C Website & Technology/Shopify Themes` and `…/Editor & Studio
Builds`, older versions go to the Trash. Every other installer (.dmg, .pkg)
and archive (.zip, …) in Downloads/Desktop goes to the Trash. App bundles
and anything outside Downloads/Desktop are left alone.

## Trashing extra copies of duplicates

```bash
python3 -m organizer audit                 # fresh scan (finds duplicates)
python3 -m organizer duplicates --open     # preview (changes nothing)
python3 -m organizer apply ~/.file-organizer/plans/trash-duplicates_….json   # type TRASH
```

Only byte-for-byte identical files (SHA-256) count. One copy of every set is
kept: organized folders beat Downloads/Desktop, a clean name beats
“copy”/“(1)”, then the older file. Right before each extra copy is trashed,
the kept copy is re-checked; if it's gone or changed, the extra stays.

## Moving screenshots into one folder

```bash
python3 -m organizer audit                  # refresh the index
python3 -m organizer screenshots --open     # preview (changes nothing)
python3 -m organizer apply ~/.file-organizer/plans/move-screenshots_….json   # type MOVE
python3 -m organizer undo  ~/.file-organizer/journal/….jsonl                 # if needed
```

Screenshots go to `~/Pictures/Screenshots`, screen recordings to
`~/Pictures/Screenshots/Screen Recordings` (change with `screenshots_folder`).
Files keep their names, dates, and quality; a name clash gets “(2)”, nothing
is overwritten. Moves stay on the same drive, and iCloud-only files are
skipped. Moving off an iCloud-synced Desktop removes them from iCloud (they
stay on this Mac and in Time Machine).

**Keep it that way automatically:** press ⌘⇧5 → **Options** → **Other
Location…** → choose `Pictures/Screenshots`. New screenshots then land there
directly, with no background service.

## Clearing out shipping labels or order files

Same three steps for both; only the preview command differs:

| Clean up | Preview command | Recognized by name |
|---|---|---|
| Shipping labels | `python3 -m organizer labels --open` | "shipping label", "USPS", "FedEx", "UPS label", "postage", Pirate Ship / Shippo / ShipStation / EasyPost |
| Order files | `python3 -m organizer orders --open` | starts with a 5–6 digit order number |

Labels saved under generic names (e.g. `Safari.pdf`) aren't caught by name;
Phase 3 will read PDF contents to find those. Files inside WhatsApp or other
apps are never touched; clear those in the app (WhatsApp: Settings → Storage
and Data → Manage Storage).

### Order files

Order files are recognized by names that start with a 5–6 digit order number
(`21074 Riley Sherman 16x16.pdf`, `#21100_proof.png`). Years like
`2024 Taxes.pdf` never match. Three steps — nothing happens until step 2:

1. **Preview** (changes nothing):
   ```bash
   python3 -m organizer audit           # refresh the index first
   python3 -m organizer orders --open
   ```
   A page lists every file with a number (#), grouped by order.
2. **Approve** — the command to copy is printed at the end of step 1:
   ```bash
   python3 -m organizer apply ~/.file-organizer/plans/trash-orders_….json
   ```
   Add `--skip 3,17,20-25` to keep specific numbers. It checks Time Machine,
   asks you to type `TRASH`, and moves files to the **Trash** with Finder
   (never permanent deletion). macOS will ask to let Terminal control Finder —
   click **OK**. Files that changed since the scan are left alone.
3. **Undo** if needed — the exact command is printed when step 2 finishes:
   ```bash
   python3 -m organizer undo ~/.file-organizer/journal/….jsonl
   ```
   Or use Finder's **Put Back** on any file in the Trash.

Files on an iCloud-synced Desktop/Documents also leave your other devices;
iCloud keeps them in "Recently Deleted" for 30 days. Emptying the Trash is
always your own decision.

### Using it with Claude Code on your Mac

Open Claude Code in the `file-organizer` folder and say something like:
*"Read the latest report in ~/.file-organizer/reports and propose the master
folder structure."* Claude reads the report locally on your Mac.

## Your setup

- **Personal photos:** Apple Photos. The library is one sealed item — never
  opened, moved, or reorganized. Personal photo organizing (albums, people,
  trips) stays in the Photos app.
- **Loose photo files:** Memobrick assets only → destined for
  `01 MEMOBRICK BUSINESS / F. Creative Assets`.
- **Screenshots:** their own category, never mixed with photos and never
  counted as Memobrick assets. Screen recordings go with them, not with
  videos. Destination: `Screenshots/` with a `Screen Recordings/` subfolder.
  Recognized by macOS names in English, Spanish, and Hebrew (and CleanShot).
  iPhone screenshots synced as `IMG_xxxx.PNG` can't be told apart by name;
  Phase 3 will detect them from image size and missing camera data.
- **Backup:** Time Machine. Before Phase 5 moves anything, the agent will check
  `tmutil latestbackup` and refuse to run if the latest backup is older than
  24 hours or the backup disk is unavailable. To check yourself: menu bar
  Time Machine icon → "Latest backup", or run `tmutil latestbackup` in Terminal.

## Configuration (`config.json`)

| Key | Meaning | Default |
|---|---|---|
| `approved_folders` | Folders allowed to be scanned (required) | — |
| `excluded_folders` | Folders inside those to skip | `[]` |
| `stale_days` | "Not touched recently" threshold | `365` |
| `hash_duplicates` | Check exact duplicates | `true` |
| `loose_photos_are_business` | Personal photos live in Apple Photos, so loose image files count as Memobrick assets (screenshots never do) | `false` (example config: `true`) |
| `label_file_pattern` | Regex searched in file names to spot shipping labels | see `organizer/plans.py` |
| `business_root` | Where Memobrick items are filed | `~/Documents/01 MEMOBRICK BUSINESS` |
| `personal_root` / `intake_root` | Where personal documents / unsorted items go | `~/Documents/02 PERSONAL DOCUMENTS`, `~/Documents/05 INTAKE` |
| `loose_file_folders` | Folders whose loose top-level files are sorted too | `~/Documents` |
| `intake_folders` | Folders `sort` files from | `~/Downloads`, `~/Desktop` |
| `screenshots_folder` | Where screenshots are gathered | `~/Pictures/Screenshots` |
| `order_file_pattern` | Regex for order-file names (first group = order number) | `^#?(\d{5,6})[ _\-]` |
| `business_keywords` | Words that mark a file as Memobrick-related | memobrick, mosaic, brick, baseplate, shopify |
| `state_dir` | Where index + reports live | `~/.file-organizer` |

## Roadmap

| Phase | What | Changes files? |
|---|---|---|
| **1. Audit** ✅ | Index, duplicates, versions, storage report | No |
| 2. Structure | Proposed master folder tree, adapted to what exists | No |
| 3. Classification | Content-based recognition (PDF/Office text, EXIF, video metadata), confidence scores, review queue, similar-image detection | No |
| 4. Preview | Exact list of proposed moves/renames, collisions flagged (order-file cleanup ✅) | No |
| 5. Execute | Approved actions only, with backup check, journal, change detection, one-command undo (Trash ✅, moves next) | **Yes, with approval** |
| 6. Search & dashboard | Natural-language search, previews, "Reveal in Finder", dashboard | No |
| 7. Automation | Watch intake folders; Review Mode / Automatic Mode for high-confidence files only; weekly report | Only per approved rules |

Never in any phase: automatic deletion, overwriting, reorganizing inside
software projects, or uploading file contents without explicit approval.

## Development

```bash
python3 -m unittest discover -s tests -t . -v
```
