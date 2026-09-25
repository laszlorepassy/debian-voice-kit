<h1 align="center">Debian Voice Kit</h1>

<p align="center">
  <strong>Give your Debian desktop a voice, and ears.</strong><br>
  Natural read-aloud for e-books, one-click MP3 audiobooks, and Win+H-style
  dictation for KDE Plasma. In English, in Hungarian, and in dozens of other
  languages.
</p>

<p align="center">
  <img alt="Debian 13" src="https://img.shields.io/badge/Debian-13%20trixie-A81D33?logo=debian&logoColor=white">
  <img alt="KDE Plasma 6" src="https://img.shields.io/badge/KDE%20Plasma-6-1D99F3?logo=kde&logoColor=white">
  <img alt="Python 3" src="https://img.shields.io/badge/Python-3-3776AB?logo=python&logoColor=white">
  <img alt="MIT License" src="https://img.shields.io/badge/license-MIT-green">
  <a href="https://paypal.me/repassyl"><img alt="Buy me a coffee" src="https://img.shields.io/badge/Buy%20me%20a%20coffee-FFDD00?logo=buymeacoffee&logoColor=black"></a>
</p>

---

Linux has had excellent open speech technology for a while. It just never
came wired up and ready to use. Debian Voice Kit does the wiring: two scripts, a
few minutes, and your laptop reads books to you in a voice you can actually
listen to for hours, and types what you say into any window.

| | What you get |
|---|---|
| **E-book reader** | The latest calibre E-book viewer, the default app for EPUB, MOBI, AZW3 and FB2 |
| **Read aloud** | Piper neural voices that run offline, on your own machine. No robotic espeak. |
| **EPUB to MP3** | Right-click a book in Dolphin to get a tagged audiobook, one MP3 per chapter, with cover art |
| **Dictation** | Press **Super+H**, speak, pause: the text lands where your cursor is, in any app |

## Quick start

```bash
git clone https://github.com/laszlorepassy/debian-voice-kit.git
cd debian-voice-kit

python3 ebook-reader/install-ebook-reader.py   # reader, voices, epub-to-mp3
python3 dictation/setup-dictation.py           # voice typing on Super+H
```

Run both as your normal user, **not** with `sudo`. They ask for your password
when they need it, and it is safe to run them again.

## Speaks your language

Everything follows the language of your desktop, so it works the same on an
English and a Hungarian Debian:

| | English desktop | Hungarian desktop |
|---|---|---|
| Read-aloud voices | lessac, ryan, amy (en_US) | anna, berta, imre (hu_HU) |
| Dictation language | English | Hungarian |
| Dolphin menu | *Make MP3 audiobook* | *MP3 hangoskönyv készítése* |
| Notifications | *Listening...* | *Figyelek...* |
| Groq key guide | English | Hungarian |

Do you read books in more than one language? Ask for both. `epub-to-mp3`
also reads the language of each book and picks a matching voice on its
own:

```bash
python3 ebook-reader/install-ebook-reader.py --lang hu en
python3 dictation/setup-dictation.py --language ""     # detect the language
```

Piper has voices for more than 50 languages (see `--list-voices`), and
Whisper understands nearly 100.

---

## E-book reader and read-aloud

`ebook-reader/install-ebook-reader.py`

Debian's calibre package can only read aloud with espeak-ng, which sounds like
a 1990s robot. This script swaps it for the latest official calibre, which has
the **Piper** neural text-to-speech engine built in:

1. Removes Debian's calibre (and the ~110 Python/Qt packages it pulled in),
   then installs the latest calibre from calibre-ebook.com into `/opt/calibre`
   with the official installer, which checks the download's signature.
2. Downloads the Piper voices of your language into
   `~/.cache/calibre/piper-voices`, so reading aloud starts instantly.
3. Sets Read aloud to Piper with your chosen voice
   (`~/.config/calibre/tts.json`).
4. Makes the E-book viewer the default app for EPUB, MOBI, AZW3 and FB2.
5. Installs `epub-to-mp3` (below), together with its Dolphin action.

```bash
python3 ebook-reader/install-ebook-reader.py
python3 ebook-reader/install-ebook-reader.py --voice ryan       # another voice
python3 ebook-reader/install-ebook-reader.py --lang en_GB       # British voices
python3 ebook-reader/install-ebook-reader.py --list-voices      # all of them
python3 ebook-reader/install-ebook-reader.py --update           # newest calibre
```

**Reading aloud:** open a book, then right-click > *Read aloud*, or press
**Ctrl+S**. To change the speed and the voice, open the Read aloud bar and
click *Configure*.

## EPUB to MP3 audiobooks

`ebook-reader/epub-to-mp3.py`, installed as the `epub-to-mp3` command

This turns a book into an audiobook for your phone, your car or a long walk.
You get one MP3 per chapter in a `<book> (MP3)` folder next to the book,
tagged with the title, album, author, track number and a cover image, so any
music player lists the chapters in order.

```bash
epub-to-mp3 book.epub [more.epub ...]          # or right-click it in Dolphin
epub-to-mp3 book.epub --voice amy --speed 1.2
epub-to-mp3 book.epub --out ~/Music/Audiobooks
```

- **The right voice for every book.** An English book is read by lessac and a
  Hungarian one by anna. For other languages calibre picks a voice, and a
  voice that is missing is downloaded first.
- **Only the story.** The cover, the title page, the table of contents and
  the copyright page are skipped.
- **You can stop and resume.** Ctrl+C removes the unfinished chapter, and
  the next run skips the chapters that are already done.
- **Small files.** 64 kbit/s mono comes to about 30 MB per hour. A chapter
  takes about a fifth of its own length to make.
- **Speed that means what it says.** `--speed 1.3` really is 30% faster,
  because the speed is calibrated against how Piper actually stretches
  speech.

## Dictation (Super+H)

`dictation/setup-dictation.py`

Voice typing like Win+H on Windows, for KDE Plasma on Wayland. Press
**Super+H** and speak. After each pause the sentence is pasted into the
focused window, whether that is a browser, LibreOffice, a terminal or a chat.
Press Super+H again to stop. It also stops by itself after 10 seconds of
silence.

```bash
python3 dictation/setup-dictation.py
python3 dictation/setup-dictation.py --language hu      # dictate in Hungarian
python3 dictation/setup-dictation.py --api-key          # replace the API key
python3 dictation/setup-dictation.py --backend openai   # use OpenAI instead
python3 dictation/setup-dictation.py --uninstall
```

The speech is transcribed by **Whisper large-v3 on Groq**, which is fast
(about a second per sentence), accurate, and has a free plan. Pauses
are detected on your machine, so only real speech is ever sent, never
silence.

<details>
<summary><strong>How it works</strong></summary>

1. **Super+H** runs `dictation toggle`, which tells the background service
   to start or stop.
2. `pw-record` (PipeWire) streams the microphone to the service.
3. Every quarter of a second the Silero voice activity detector (from
   `faster-whisper`, with no Whisper model downloaded) checks whether you are
   speaking. After 1.5 seconds of silence the sentence counts as finished.
   Longer speech is cut at 20 seconds.
4. The sentence goes to Groq as a WAV file, together with the previous
   sentence as context, and the text comes back.
5. The text is put on the clipboard, and a virtual keyboard (`/dev/uinput`,
   no extra packages) presses **Shift+Insert**, which pastes in GTK and Qt
   apps, in browsers and in terminals alike. When you stop, your previous
   clipboard content is restored.
6. Phrases that Whisper tends to invent from background noise, such as
   "Thanks for watching!", are dropped.

What the installer sets up: the Debian packages, a udev rule for the virtual
keyboard, a Python venv in `~/.local/share/dictation`, the `dictation`
systemd user service, the Super+H shortcut, and a microphone check that
turns the input volume down if the recording is clipped.

</details>

Settings are in `~/.config/dictation/config.json`. After editing them, run
`systemctl --user restart dictation`. To watch the log, run
`journalctl --user -u dictation -f`.

### Getting your free Groq API key

Dictation needs an API key from Groq. It is free, needs no credit card and
takes about a minute. **The setup script does most of the work for you:**

1. It opens **https://console.groq.com/keys** in your browser.
2. **You** sign in with Google, GitHub or your e-mail address.
3. **You** click **Create API Key**, type any name (for example `dictation`)
   and click **Submit**.
4. **You** click the copy button next to the new key. Groq shows the key
   only once.

That is all. The script **notices the key on the clipboard by itself**,
**checks it** with Groq, **saves it** to `~/.config/dictation/api-key`
(readable only by you) and **clears it from the clipboard**. You can also
paste the key into the terminal instead (it stays hidden) and press Enter.

The script explains these steps in English or in Hungarian, whichever your
desktop uses. Some more tips:

- **Fully unattended:** if the key is in `$GROQ_API_KEY` (or
  `$OPENAI_API_KEY` with `--backend openai`), the script checks it and uses
  it without asking:
  ```bash
  GROQ_API_KEY=gsk_... python3 dictation/setup-dictation.py
  ```
- **A new key:** run `setup-dictation.py --api-key`. If a key ever leaks,
  delete it on the same Groq page and create a new one.
- **Limits:** the free plan has rate limits that are far above what one
  person can dictate. You can see yours at
  https://console.groq.com/settings/limits. If you do reach one, a
  notification says *API limit reached*.
- **If it stops working:** a notification tells you when the key is
  missing or invalid, or when Groq cannot be reached.

> **Privacy:** read-aloud and the MP3 audiobooks run entirely offline.
> Dictation sends your speech (only the parts where you speak) to Groq, or to
> OpenAI if you choose it. Do not dictate anything you would not send to
> that service.

---

## Requirements

- **Debian 13 (trixie)** with **KDE Plasma 6**. Dictation needs a Wayland
  session, which is the default.
- An internet connection for the installation. After that, only dictation
  needs one.
- About 1 GB of disk space for calibre and the voices.

Other Debian-based systems with Plasma 6 will probably work too, but they
have not been tested.

## Magyarul

A Debian Voice Kit magyar és angol Debian KDE rendszeren egyaránt működik: a
felolvasó magyar hangokkal (anna, berta, imre) olvas, a diktálás magyarul ír,
az értesítések és a Groq API-kulcs beszerzésének lépései is magyarul
jelennek meg. Két parancs az egész, a Gyors kezdés (*Quick start*) résznél.

## Support

If Debian Voice Kit saves you some typing or reads you a good book, you can
buy me a coffee:

<a href="https://paypal.me/repassyl"><img src="https://img.shields.io/badge/Buy%20me%20a%20coffee-FFDD00?style=for-the-badge&logoColor=black" alt="Buy me a coffee"></a>

Bug reports and pull requests are welcome.

## Credits

Debian Voice Kit is built on [calibre](https://calibre-ebook.com) by Kovid Goyal,
the [Piper](https://github.com/rhasspy/piper) voices,
[Whisper](https://github.com/openai/whisper) on [Groq](https://groq.com),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper)'s Silero VAD,
PipeWire, wl-clipboard and ffmpeg.

## License

[MIT](LICENSE) © Répássy László
