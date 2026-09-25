#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
epub-to-mp3.py
==============
Turns EPUB books into MP3 audiobooks, read by calibre's Piper neural voices
(calibre is installed by install-ebook-reader.py): one MP3 per chapter, in
a folder next to the book, with title, album, author, track number and
cover, so a phone's music player lists them in order.

    Dune.epub  ->  Dune (MP3)/01 - Book One.mp3
                              02 - Chapter 1.mp3 ...

The voice follows the language of the book (an English book: lessac, a
Hungarian one: anna, any other language: calibre's voice for it); a voice
that is not on the machine yet is downloaded first. --voice picks one.

The text of every chapter file of the book is read, except the cover, the
title page and the table of contents; a pause is left between paragraphs.
Speech takes roughly a fifth of its own length to make, so a 300-page book
takes a while; the progress is printed as it goes.

It runs inside calibre's Python (/opt/calibre/calibre-debug), which has
Piper built in, and encodes with ffmpeg. No sudo needed.

USAGE:
    python3 epub-to-mp3.py book.epub [more.epub ...]
    python3 epub-to-mp3.py book.epub --voice ryan --speed 1.2
    python3 epub-to-mp3.py book.epub --out ~/Music/Audiobooks
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys

CALIBRE_DEBUG = "/opt/calibre/calibre-debug"
# calibre-debug takes no options for the script, so they are passed here.
ARGS_ENV = "EPUB_TO_MP3_ARGS"
# The voice of a book's language (ISO 639-2 code, as calibre stores it);
# other languages get calibre's own voice for them.
LANGUAGE_VOICES = {"eng": "lessac", "hun": "anna"}

SAMPLE_RATE = 22050      # what the Piper voices produce
BITRATE = "64k"          # mono speech; about 30 MB per hour
PARAGRAPH_PAUSE = 0.6    # seconds
SENTENCE_PAUSE = 0.2
# About this share of Piper's speech does not change with its length scale
# (measured: length scale 0.5 / 1 / 2 gave 5.6 / 8.7 / 14.7 seconds).
FIXED_SHARE = 0.3

BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd",
              "div", "blockquote", "pre", "td", "th", "caption",
              "figcaption", "section", "article", "aside", "header",
              "footer", "tr", "table", "ul", "ol", "dl", "figure"}
SKIP_TAGS = {"script", "style", "head", "svg", "math", "rt"}
# Guide/landmark types that are not read aloud.
SKIP_TYPES = {"cover", "title-page", "toc", "copyright-page"}
# The same by table of contents title, for books without those marks.
SKIP_TITLES = {"borító", "címoldal", "tartalom", "tartalomjegyzék",
               "impresszum", "copyright", "cover", "title page",
               "contents", "table of contents"}
COVER_SIZE = 600         # pixels, the cover is stored in every MP3


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Make MP3 audiobooks from EPUB files with calibre's "
                    "Piper neural voices.")
    parser.add_argument("books", nargs="+", help="EPUB files")
    parser.add_argument("--voice", default="",
                        help="Piper voice, e.g. lessac, ryan, amy (English) "
                             "or anna, berta, imre (Hungarian); default: "
                             "the one of the book's language")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="0.6 .. 2.0, 1.0 is normal (default)")
    parser.add_argument("--out", help="folder for the MP3 folders "
                                      "(default: next to each book)")
    args = parser.parse_args(argv)
    if not 0.6 <= args.speed <= 2.0:
        parser.error("--speed must be between 0.6 and 2.0")
    for book in args.books:
        if not os.path.isfile(book):
            parser.error("file not found: %s" % book)
    return args


# ------------------------------------------------------- outside calibre

def relaunch_in_calibre(args):
    """Run this file again with calibre's Python, which has Piper."""
    if not os.path.exists(CALIBRE_DEBUG):
        sys.exit("calibre is not in /opt/calibre; run "
                 "install-ebook-reader.py first.")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is missing: sudo apt-get install ffmpeg")
    options = {"books": [os.path.abspath(b) for b in args.books],
               "voice": args.voice, "speed": args.speed,
               "out": os.path.abspath(os.path.expanduser(args.out))
               if args.out else None}
    env = dict(os.environ, **{ARGS_ENV: json.dumps(options)})
    # Ctrl+C is handled inside calibre (it removes the unfinished MP3);
    # this process only waits for it.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    result = subprocess.run(
        [CALIBRE_DEBUG, "-e", os.path.abspath(__file__)], env=env,
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    sys.exit(result.returncode)


# -------------------------------------------------------- inside calibre

def safe_filename(text, limit=80):
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", text)
    text = " ".join(text.split()).strip(" .")
    return text[:limit].rstrip(" .") or "chapter"


def local(tag):
    return tag.rpartition("}")[2].lower() if isinstance(tag, str) else ""


def paragraphs(root):
    """The text of the body, one string per block (paragraph, heading...)."""
    body = next((e for e in root.iter() if local(e.tag) == "body"), None)
    if body is None:
        return []
    out = []
    current = []

    def flush():
        text = " ".join("".join(current).split())
        if text:
            out.append(text)
        current.clear()

    def walk(elem):
        tag = local(elem.tag)
        if tag in SKIP_TAGS or not isinstance(elem.tag, str):
            current.append(" ")
            return
        block = tag in BLOCK_TAGS
        if block:
            flush()
        if tag == "br":
            current.append(" ")
        if elem.text:
            current.append(elem.text)
        for child in elem:
            walk(child)
            if child.tail:
                current.append(child.tail)
        if block:
            flush()

    walk(body)
    flush()
    return out


def skipped_names(container):
    """Cover, title page and table of contents files of the book."""
    names = set()
    for ref in container.opf_xpath("//opf:guide/opf:reference[@href]"):
        if ref.get("type", "").lower() in SKIP_TYPES:
            names.add(container.href_to_name(
                ref.get("href").partition("#")[0], container.opf_name))
    try:
        from calibre.ebooks.oeb.polish.toc import get_landmarks
        for lm in get_landmarks(container):
            if lm.get("type", "").lower() in SKIP_TYPES:
                names.add(lm["dest"])
    except Exception:
        pass
    return names


def chapter_titles(container):
    """File name -> title of the first table of contents entry in it."""
    from calibre.ebooks.oeb.polish.toc import get_toc
    titles = {}
    for node in get_toc(container).iterdescendants():
        if node.dest and node.title and node.dest not in titles:
            titles[node.dest] = " ".join(node.title.split())
    return titles


def cover_image(container, workdir):
    from calibre.ebooks.oeb.polish.cover import find_cover_image
    try:
        name = find_cover_image(container)
    except Exception:
        name = None
    if not name or not container.exists(name):
        return None
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        return None
    original = os.path.join(workdir, "original" + ext)
    with container.open(name, "rb") as src, open(original, "wb") as dst:
        shutil.copyfileobj(src, dst)
    path = os.path.join(workdir, "cover.jpg")
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", original,
         "-vf", "scale='min(%d,iw)':-2" % COVER_SIZE, "-q:v", "4", path],
        env=clean_env())
    return path if result.returncode == 0 else None


def clean_env():
    # calibre-debug sets LD_LIBRARY_PATH to its own libraries, whose older
    # libav* would break Debian's ffmpeg.
    return {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}


def silence(seconds):
    return b"\0\0" * int(SAMPLE_RATE * seconds)


def encode(pcm_chunks, path, tags, cover):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "-"]
    if cover:
        cmd += ["-i", cover, "-map", "0:a", "-map", "1:v", "-c:v", "copy",
                "-disposition:v", "attached_pic",
                "-metadata:s:v", "title=Cover",
                "-metadata:s:v", "comment=Cover (front)"]
    cmd += ["-c:a", "libmp3lame", "-b:a", BITRATE, "-id3v2_version", "3"]
    for key, value in tags.items():
        cmd += ["-metadata", "%s=%s" % (key, value)]
    part = path + ".part.mp3"
    ffmpeg = subprocess.Popen(cmd + [part], stdin=subprocess.PIPE,
                              env=clean_env())
    seconds = 0.0
    try:
        for chunk in pcm_chunks:
            ffmpeg.stdin.write(chunk)
            seconds += len(chunk) / 2 / SAMPLE_RATE
        ffmpeg.stdin.close()
    except BaseException:
        try:
            ffmpeg.stdin.close()
        except OSError:
            pass
        ffmpeg.kill()
        ffmpeg.wait()
        if os.path.exists(part):
            os.remove(part)
        raise
    if ffmpeg.wait() != 0:
        raise RuntimeError("ffmpeg failed on %s" % path)
    os.replace(part, path)
    return seconds


def convert_book(book, options, piper):
    import tempfile
    from calibre.ebooks.oeb.polish.container import get_container

    print("\n==> %s" % book)
    container = get_container(book, tweak_mode=True)
    title = container.mi.title or os.path.splitext(os.path.basename(book))[0]
    author = " & ".join(a for a in (container.mi.authors or [])
                        if a and a != "Unknown")
    skip = skipped_names(container)
    titles = chapter_titles(container)

    # A book without a language gets the voice of calibre's language.
    language = (container.mi.languages or [""])[0]
    language = "" if language == "und" else language
    voice = piper.resolve_voice(
        language, options["voice"] or LANGUAGE_VOICES.get(language, ""))
    print("    voice: %s (%s)" % (voice.human_name, voice.name.split(":")[0]))
    if not piper.ensure_voices_downloaded([(language, voice.human_name)]):
        print("! could not download the voice %s" % voice.human_name)
        return

    chapters = []
    for name, linear in container.spine_names:
        if name in skip or titles.get(name, "").lower() in SKIP_TITLES:
            continue
        paras = paragraphs(container.parsed(name))
        if sum(len(p) for p in paras) < 40:
            continue
        chapters.append((name, paras))
    if not chapters:
        print("! no text found in %s" % book)
        return

    base = options["out"] or os.path.dirname(book)
    folder = os.path.join(base, safe_filename(
        os.path.splitext(os.path.basename(book))[0], 120) + " (MP3)")
    os.makedirs(folder, exist_ok=True)
    for leftover in os.listdir(folder):
        if leftover.endswith(".part.mp3"):
            os.remove(os.path.join(folder, leftover))
    total_chars = sum(len(p) for _, paras in chapters for p in paras)
    print("    %d chapters, %d characters -> %s"
          % (len(chapters), total_chars, folder))

    done_chars = 0
    width = max(2, len(str(len(chapters))))
    with tempfile.TemporaryDirectory() as workdir:
        cover = cover_image(container, workdir)
        for number, (name, paras) in enumerate(chapters, 1):
            chapter = titles.get(name) or paras[0][:80]
            path = os.path.join(folder, "%0*d - %s.mp3" % (
                width, number, safe_filename(chapter)))
            chars = sum(len(p) for p in paras)
            if os.path.exists(path):
                print("    [%d/%d] already done: %s"
                      % (number, len(chapters), os.path.basename(path)))
                done_chars += chars
                continue

            def pcm():
                for para in paras:
                    for data, _ in piper.text_to_raw_audio_data(
                            [para], lang=language,
                            voice_name=voice.human_name,
                            sample_rate=SAMPLE_RATE):
                        yield data
                    yield silence(PARAGRAPH_PAUSE / options["speed"])

            tags = {"title": chapter, "album": title,
                    "track": "%d/%d" % (number, len(chapters)),
                    "genre": "Audiobook"}
            if author:
                tags["artist"] = tags["album_artist"] = author
            print("    [%d/%d] %s ..." % (number, len(chapters), chapter),
                  end="", flush=True)
            seconds = encode(pcm(), path, tags, cover)
            done_chars += chars
            print(" %d:%02d  (%d%% of the book)"
                  % (seconds // 60, seconds % 60,
                     100 * done_chars // total_chars))
    print("    done: %s" % folder)


def main_in_calibre(options):
    sys.stdout.reconfigure(line_buffering=True)
    from calibre.gui2.tts.piper import PiperEmbedded

    piper = PiperEmbedded()
    if options["voice"] and \
            options["voice"] not in piper.human_voice_name_map:
        sys.exit("Unknown voice '%s'. Some voices: lessac, ryan, amy "
                 "(English), anna, berta, imre (Hungarian); all of them: "
                 "install-ebook-reader.py --list-voices" % options["voice"])
    # calibre multiplies Piper's length scale by (1 - rate), but only part
    # of the speech follows it; this length scale gives the asked speed.
    # The pauses are shortened with the speech.
    scale = (1 / options["speed"] - FIXED_SHARE) / (1 - FIXED_SHARE)
    piper._embedded_settings = piper._embedded_settings._replace(
        rate=max(-1.0, min(0.9, 1 - scale)),
        sentence_delay=SENTENCE_PAUSE / options["speed"])
    try:
        for book in options["books"]:
            convert_book(book, options, piper)
    except KeyboardInterrupt:
        sys.exit("\nStopped. Run it again to continue from the chapter "
                 "that was not finished.")
    finally:
        piper.shutdown()


if __name__ == "__main__":
    if ARGS_ENV in os.environ:
        main_in_calibre(json.loads(os.environ.pop(ARGS_ENV)))
    else:
        relaunch_in_calibre(parse_args(sys.argv[1:]))
