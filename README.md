# Smart File Organization Agent

A local-first, safety-first file organizer for macOS, built for the
Memobrick business and personal files.

> **Status: audit + first cleanup (order files).**
> `audit`, `report`, and `orders` only *look*: they write an index, reports,
> and previews inside `~/.file-organizer/` (readable only by your account).
> The only command that changes files is `apply`, and only for a plan you
> reviewed, after a Time Machine check and typing `TRASH`. It moves files to
> the Trash (never permanent deletion), and `undo` puts them back.

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

## Clearing out customer order files

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
