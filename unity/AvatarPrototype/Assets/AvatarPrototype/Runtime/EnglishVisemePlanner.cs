using System;
using System.Collections.Generic;

namespace PersonalAssistant.Avatar
{
    public readonly struct EnglishVisemeCue
    {
        public readonly int CharacterIndex;
        public readonly float Time;
        public readonly string Name;
        public readonly float Open;
        public readonly float Width;

        public EnglishVisemeCue(int characterIndex, float time, string name, float open, float width)
        {
            CharacterIndex = characterIndex;
            Time = time;
            Name = name;
            Open = open;
            Width = width;
        }
    }

    /// <summary>
    /// Produces a lightweight English viseme timeline from written text. Android TTS
    /// range callbacks re-anchor this estimate to the audio while it is being spoken.
    /// </summary>
    public static class EnglishVisemePlanner
    {
        public static List<EnglishVisemeCue> Build(string text, out float duration)
        {
            List<EnglishVisemeCue> cues = new();
            string source = text ?? string.Empty;
            float time = 0f;
            cues.Add(CreateCue(0, time, "sil"));
            time += 0.06f;

            for (int index = 0; index < source.Length;)
            {
                char current = char.ToLowerInvariant(source[index]);
                if (char.IsWhiteSpace(current))
                {
                    time += 0.045f;
                    index++;
                    continue;
                }

                if (!char.IsLetterOrDigit(current))
                {
                    time += IsSentenceBreak(current) ? 0.22f : 0.11f;
                    cues.Add(CreateCue(index, time, "sil"));
                    index++;
                    continue;
                }

                string viseme = ResolveViseme(source, index, out int consumed);
                cues.Add(CreateCue(index, time, viseme));
                time += DurationFor(viseme);
                index += consumed;
            }

            cues.Add(CreateCue(source.Length, time, "sil"));
            duration = time + 0.16f;
            return cues;
        }

        public static float FindTimeForCharacter(IReadOnlyList<EnglishVisemeCue> cues, int characterIndex)
        {
            if (cues == null || cues.Count == 0) return 0f;
            for (int i = 0; i < cues.Count; i++)
                if (cues[i].CharacterIndex >= characterIndex) return cues[i].Time;
            return cues[cues.Count - 1].Time;
        }

        private static string ResolveViseme(string source, int index, out int consumed)
        {
            char current = char.ToLowerInvariant(source[index]);
            char next = index + 1 < source.Length ? char.ToLowerInvariant(source[index + 1]) : '\0';
            char afterNext = index + 2 < source.Length ? char.ToLowerInvariant(source[index + 2]) : '\0';
            consumed = 1;

            if (current == 't' && next == 'c' && afterNext == 'h') { consumed = 3; return "CH"; }
            if (current == 'd' && next == 'g' && afterNext == 'e') { consumed = 3; return "CH"; }
            if (current == 't' && next == 'h') { consumed = 2; return "TH"; }
            if ((current == 's' && next == 'h') || (current == 'c' && next == 'h')) { consumed = 2; return "CH"; }
            if (current == 'p' && next == 'h') { consumed = 2; return "FF"; }
            if (current == 'n' && next == 'g') { consumed = 2; return "nn"; }
            if (current == 'o' && next == 'o') { consumed = 2; return "ou"; }
            if ((current == 'e' && next == 'e') || (current == 'e' && next == 'a')) { consumed = 2; return "E"; }
            if ((current == 'o' && next == 'w') || (current == 'o' && next == 'u')) { consumed = 2; return "oh"; }

            return current switch
            {
                'p' or 'b' or 'm' => "PP",
                'f' or 'v' => "FF",
                't' or 'd' => "DD",
                'k' or 'g' or 'c' or 'q' => "kk",
                'j' => "CH",
                's' or 'z' or 'x' => "SS",
                'n' => "nn",
                'r' or 'w' => "RR",
                'h' => "HH",
                'l' => "LL",
                'a' => "aa",
                'e' => "E",
                'i' or 'y' => "ih",
                'o' => "oh",
                'u' => "ou",
                _ => "sil"
            };
        }

        private static EnglishVisemeCue CreateCue(int index, float time, string name)
        {
            (float open, float width) = name switch
            {
                "PP" => (0.025f, 0.62f),
                "FF" => (0.08f, 0.88f),
                "TH" => (0.12f, 0.78f),
                "DD" => (0.08f, 0.80f),
                "kk" => (0.15f, 0.82f),
                "CH" => (0.11f, 0.92f),
                "SS" => (0.07f, 1.14f),
                "nn" => (0.09f, 0.76f),
                "RR" => (0.14f, 0.74f),
                "aa" => (0.36f, 0.96f),
                "E" => (0.22f, 1.10f),
                "ih" => (0.17f, 0.90f),
                "oh" => (0.30f, 0.80f),
                "ou" => (0.21f, 0.66f),
                "HH" => (0.20f, 0.94f),
                "LL" => (0.10f, 0.78f),
                _ => (0.035f, 0.72f)
            };
            return new EnglishVisemeCue(index, time, name, open, width);
        }

        private static float DurationFor(string name)
        {
            return name switch
            {
                "aa" or "E" or "ih" or "oh" or "ou" => 0.115f,
                "PP" => 0.072f,
                "sil" => 0.06f,
                _ => 0.085f
            };
        }

        private static bool IsSentenceBreak(char value) => value is '.' or '!' or '?';
    }
}
