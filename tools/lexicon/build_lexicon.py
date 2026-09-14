"""Builds Milo's compact English pronunciation lexicon from CMUdict.

Run from the Android assistant repository root:

    python3 tools/lexicon/build_lexicon.py

The output keeps the most frequent spoken English words plus assistant vocabulary.
Each line is `word PH1 PH2 ...` using ARPAbet with CMUdict stress digits.
"""

from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "unity" / "AvatarPrototype" / "Assets" / "AvatarPrototype" / "Resources" / "Speech" / "en_lexicon.txt"
CMUDICT_URL = "https://raw.githubusercontent.com/cmusphinx/cmudict/master/cmudict.dict"
FREQUENCY_URL = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/en/en_50k.txt"
WORD_LIMIT = 30000

# Words an assistant says often that may rank low in subtitle frequency data.
ASSISTANT_VOCABULARY = """
milo assistant calendar schedule meeting meetings reminder reminders email emails inbox
message messages notification notifications appointment appointments deadline task tasks
weather forecast temperature traffic commute summary summarize document documents folder
upload download sync synced settings battery wifi bluetooth navigation directions restaurant
reservation invoice receipt booking flight flights hotel itinerary priority project projects
kotlin android unity backend server servers deploy deployment dashboard browser
""".split()

MILO_OVERRIDES = {
    "milo": "M AY1 L OW0",
    "wifi": "W AY1 F AY0",
    "kotlin": "K AA1 T L IH0 N",
}


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def load_cmudict():
    entries = {}
    for line in fetch(CMUDICT_URL).splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        word, *phones = line.split()
        if "(" in word:
            continue  # Keep the primary pronunciation only.
        entries[word.lower()] = " ".join(phones)
    return entries


def main():
    cmudict = load_cmudict()
    selected = {}
    for line in fetch(FREQUENCY_URL).splitlines()[:WORD_LIMIT]:
        word = line.split(" ", 1)[0].lower()
        if word in cmudict:
            selected[word] = cmudict[word]
    for word in ASSISTANT_VOCABULARY:
        if word in cmudict:
            selected[word] = cmudict[word]
    selected.update(MILO_OVERRIDES)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("# Derived from CMUdict (Copyright 1993-2015 Carnegie Mellon University, BSD-style licence).\n")
        for word in sorted(selected):
            handle.write(f"{word} {selected[word]}\n")
    print(f"LEXICON_BUILD_COMPLETE words={len(selected)} path={OUTPUT} bytes={OUTPUT.stat().st_size}")


if __name__ == "__main__":
    main()
