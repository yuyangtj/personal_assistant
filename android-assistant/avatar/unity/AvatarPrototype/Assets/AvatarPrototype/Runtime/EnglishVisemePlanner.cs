using System;
using System.Collections.Generic;

namespace PersonalAssistant.Avatar
{
    public readonly struct EnglishVisemeCue
    {
        public readonly int CharacterIndex;
        public readonly float Time;
        public readonly float Duration;
        public readonly string Name;
        public readonly float Strength;

        public EnglishVisemeCue(int characterIndex, float time, float duration, string name, float strength)
        {
            CharacterIndex = characterIndex;
            Time = time;
            Duration = duration;
            Name = name;
            Strength = strength;
        }

        public float End => Time + Duration;
    }

    /// <summary>Audio start time of a word range reported by the TTS engine.</summary>
    public readonly struct TtsWordTiming
    {
        public readonly int Start;
        public readonly int End;
        public readonly float Time;

        public TtsWordTiming(int start, int end, float time)
        {
            Start = start;
            End = end;
            Time = time;
        }
    }

    /// <summary>
    /// Builds an English viseme timeline from pronunciation. With TTS word timings the
    /// sounds of each word are fitted between that word's measured audio start and the
    /// next word's start; without them, a natural speaking-rate estimate is used.
    /// </summary>
    public static class EnglishVisemePlanner
    {
        private const float SpeechRate = 0.95f;
        private const float MinimumPause = 0.08f;

        private readonly struct Sound
        {
            public readonly string Viseme;
            public readonly float Duration;
            public readonly float Strength;

            public Sound(string viseme, float duration, float strength)
            {
                Viseme = viseme;
                Duration = duration;
                Strength = strength;
            }
        }

        public static List<EnglishVisemeCue> Build(string text, out float duration)
        {
            string source = text ?? string.Empty;
            List<EnglishVisemeCue> cues = new() { new EnglishVisemeCue(0, 0f, 0.05f, "sil", 0f) };
            float time = 0.05f;
            int previousEnd = 0;

            foreach (SpokenWord word in EnglishPronouncer.Tokenize(source))
            {
                float pause = PauseBetween(source, previousEnd, word.Start);
                if (pause > 0f)
                {
                    cues.Add(new EnglishVisemeCue(previousEnd, time, pause, "sil", 0f));
                    time += pause;
                }
                time = AppendWord(cues, word, ToSounds(word.Phones), time, 1f / SpeechRate);
                previousEnd = word.End;
            }

            cues.Add(new EnglishVisemeCue(source.Length, time, 0.16f, "sil", 0f));
            duration = time + 0.16f;
            return cues;
        }

        public static List<EnglishVisemeCue> Build(string text, IReadOnlyList<TtsWordTiming> timings, float audioDuration)
        {
            if (timings == null || timings.Count == 0) return Stretch(Build(text, out float estimate), audioDuration / Math.Max(estimate, 0.01f));

            string source = text ?? string.Empty;
            List<SpokenWord> words = EnglishPronouncer.Tokenize(source);
            List<EnglishVisemeCue> cues = new() { new EnglishVisemeCue(0, 0f, 0f, "sil", 0f) };

            for (int index = 0; index < words.Count;)
            {
                // Words the engine grouped into one range share that range's audio window.
                int timingIndex = TimingFor(timings, words[index].Start);
                int groupEnd = index + 1;
                while (groupEnd < words.Count && TimingFor(timings, words[groupEnd].Start) == timingIndex) groupEnd++;

                float windowStart = timingIndex < 0 ? 0f : timings[timingIndex].Time;
                float windowEnd = timingIndex + 1 < timings.Count ? timings[timingIndex + 1].Time : audioDuration;
                if (groupEnd < words.Count)
                {
                    int nextTiming = TimingFor(timings, words[groupEnd].Start);
                    if (nextTiming >= 0) windowEnd = Math.Min(windowEnd, timings[nextTiming].Time);
                }
                float available = Math.Max(0.04f, windowEnd - windowStart);

                List<Sound>[] groupSounds = new List<Sound>[groupEnd - index];
                float natural = 0f;
                for (int i = index; i < groupEnd; i++)
                {
                    groupSounds[i - index] = ToSounds(words[i].Phones);
                    foreach (Sound sound in groupSounds[i - index]) natural += sound.Duration;
                }

                // Stretch slightly for slow delivery; any remaining window time is a pause.
                float scale = natural <= 0f ? 1f : Math.Min(available / natural, 1.35f);
                float time = windowStart;
                for (int i = index; i < groupEnd; i++)
                    time = AppendWord(cues, words[i], groupSounds[i - index], time, scale);

                if (windowEnd - time >= MinimumPause)
                    cues.Add(new EnglishVisemeCue(words[groupEnd - 1].End, time, windowEnd - time, "sil", 0f));
                index = groupEnd;
            }

            float lastEnd = cues[^1].End;
            cues.Add(new EnglishVisemeCue(source.Length, Math.Max(lastEnd, audioDuration), 0.16f, "sil", 0f));
            return cues;
        }

        private static List<EnglishVisemeCue> Stretch(List<EnglishVisemeCue> cues, float scale)
        {
            // Without word ranges, the estimate is still pinned to the real audio length.
            for (int i = 0; i < cues.Count; i++)
            {
                EnglishVisemeCue cue = cues[i];
                cues[i] = new EnglishVisemeCue(cue.CharacterIndex, cue.Time * scale, cue.Duration * scale, cue.Name, cue.Strength);
            }
            return cues;
        }

        public static float FindTimeForCharacter(IReadOnlyList<EnglishVisemeCue> cues, int characterIndex)
        {
            if (cues == null || cues.Count == 0) return 0f;
            for (int i = 0; i < cues.Count; i++)
                if (cues[i].CharacterIndex >= characterIndex) return cues[i].Time;
            return cues[^1].Time;
        }

        private static int TimingFor(IReadOnlyList<TtsWordTiming> timings, int characterIndex)
        {
            int match = -1;
            for (int i = 0; i < timings.Count; i++)
            {
                if (timings[i].Start > characterIndex) break;
                match = i;
            }
            return match;
        }

        private static float AppendWord(List<EnglishVisemeCue> cues, SpokenWord word, List<Sound> sounds, float time, float scale)
        {
            foreach (Sound sound in sounds)
            {
                float length = sound.Duration * scale;
                cues.Add(new EnglishVisemeCue(word.Start, time, length, sound.Viseme, sound.Strength));
                time += length;
            }
            return time;
        }

        private static float PauseBetween(string source, int from, int to)
        {
            float pause = 0f;
            for (int i = from; i < to && i < source.Length; i++)
            {
                char value = source[i];
                if (value is '.' or '!' or '?') pause = Math.Max(pause, 0.30f);
                else if (value is ',' or ';' or ':' or '—' or '-') pause = Math.Max(pause, 0.15f);
            }
            return pause;
        }

        private static List<Sound> ToSounds(string[] phones)
        {
            List<Sound> sounds = new(phones.Length + 4);
            for (int i = 0; i < phones.Length; i++)
            {
                string phone = phones[i];
                char last = phone[^1];
                bool isVowel = char.IsDigit(last);
                string basePhone = isVowel ? phone[..^1] : phone;
                bool stressed = isVowel && last != '0';
                float vowelLength = stressed ? 0.13f : 0.075f;
                float vowelStrength = stressed ? 1f : 0.72f;

                switch (basePhone)
                {
                    case "AA": case "AE": case "AH": sounds.Add(new Sound("aa", vowelLength, vowelStrength)); break;
                    case "AO": sounds.Add(new Sound("oh", vowelLength, vowelStrength)); break;
                    case "EH": sounds.Add(new Sound("E", vowelLength, vowelStrength)); break;
                    case "ER": sounds.Add(new Sound("RR", vowelLength, vowelStrength)); break;
                    case "IH": case "IY": sounds.Add(new Sound("ih", vowelLength, vowelStrength)); break;
                    case "UH": case "UW": sounds.Add(new Sound("ou", vowelLength, vowelStrength)); break;
                    case "AY": AddDiphthong(sounds, "aa", "ih", stressed, vowelStrength); break;
                    case "AW": AddDiphthong(sounds, "aa", "ou", stressed, vowelStrength); break;
                    case "EY": AddDiphthong(sounds, "E", "ih", stressed, vowelStrength); break;
                    case "OW": AddDiphthong(sounds, "oh", "ou", stressed, vowelStrength); break;
                    case "OY": AddDiphthong(sounds, "oh", "ih", stressed, vowelStrength); break;
                    case "P": case "B": case "M": sounds.Add(new Sound("PP", basePhone == "M" ? 0.075f : 0.07f, 1f)); break;
                    case "F": case "V": sounds.Add(new Sound("FF", 0.085f, 1f)); break;
                    case "TH": sounds.Add(new Sound("TH", 0.085f, 1f)); break;
                    case "DH": sounds.Add(new Sound("TH", 0.055f, 0.9f)); break;
                    case "T": case "D": sounds.Add(new Sound("DD", 0.065f, 0.85f)); break;
                    case "K": case "G": sounds.Add(new Sound("kk", 0.07f, 0.8f)); break;
                    case "NG": sounds.Add(new Sound("kk", 0.07f, 0.7f)); break;
                    case "CH": case "JH": sounds.Add(new Sound("CH", 0.10f, 1f)); break;
                    case "SH": case "ZH": sounds.Add(new Sound("CH", 0.095f, 1f)); break;
                    case "S": case "Z": sounds.Add(new Sound("SS", 0.09f, 0.95f)); break;
                    case "N": sounds.Add(new Sound("nn", 0.065f, 0.8f)); break;
                    case "L": sounds.Add(new Sound("nn", 0.06f, 0.75f)); break;
                    case "R": sounds.Add(new Sound("RR", 0.06f, 0.85f)); break;
                    case "W": sounds.Add(new Sound("ou", 0.06f, 0.9f)); break;
                    case "Y": sounds.Add(new Sound("ih", 0.05f, 0.7f)); break;
                    case "HH": sounds.Add(new Sound(NextVowelViseme(phones, i), 0.05f, 0.45f)); break;
                }
            }
            return sounds;
        }

        private static void AddDiphthong(List<Sound> sounds, string first, string second, bool stressed, float strength)
        {
            float length = stressed ? 0.18f : 0.10f;
            sounds.Add(new Sound(first, length * 0.62f, strength));
            sounds.Add(new Sound(second, length * 0.38f, strength * 0.8f));
        }

        private static string NextVowelViseme(string[] phones, int index)
        {
            // H is shaped by the vowel that follows it ("hi" opens, "who" rounds).
            for (int i = index + 1; i < phones.Length; i++)
            {
                if (!char.IsDigit(phones[i][^1])) continue;
                List<Sound> vowel = ToSounds(new[] { phones[i] });
                return vowel.Count > 0 ? vowel[0].Viseme : "aa";
            }
            return "aa";
        }
    }
}
