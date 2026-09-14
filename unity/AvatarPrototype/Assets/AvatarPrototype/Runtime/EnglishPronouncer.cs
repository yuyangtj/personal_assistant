using System;
using System.Collections.Generic;
using UnityEngine;

namespace PersonalAssistant.Avatar
{
    public readonly struct SpokenWord
    {
        public readonly int Start;
        public readonly int End;
        public readonly string[] Phones;

        public SpokenWord(int start, int end, string[] phones)
        {
            Start = start;
            End = end;
            Phones = phones;
        }
    }

    /// <summary>
    /// Converts English text into ARPAbet phones while preserving character offsets, so
    /// Android TTS word ranges can be matched to the sounds that make up each word.
    /// Known words use the bundled CMUdict subset; unknown words use spelling rules.
    /// </summary>
    public static class EnglishPronouncer
    {
        private const string LexiconResource = "Speech/en_lexicon";
        private static Dictionary<string, string[]> lexicon;

        private static readonly string[] Ones =
        {
            "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"
        };

        private static readonly string[] Tens = { "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety" };

        public static bool HasLexicon => Lexicon.Count > 0;

        public static void SetLexicon(string text)
        {
            lexicon = Parse(text);
        }

        public static List<SpokenWord> Tokenize(string text)
        {
            List<SpokenWord> words = new();
            string source = text ?? string.Empty;
            for (int index = 0; index < source.Length;)
            {
                if (!char.IsLetterOrDigit(source[index]))
                {
                    index++;
                    continue;
                }

                int start = index;
                bool numeric = char.IsDigit(source[index]);
                while (index < source.Length && IsWordCharacter(source, index, numeric)) index++;
                string token = source.Substring(start, index - start).Trim('\'', '’');
                string[] phones = numeric ? PronounceNumber(token) : Pronounce(token);
                if (phones.Length > 0) words.Add(new SpokenWord(start, index, phones));
            }
            return words;
        }

        public static string[] Pronounce(string word)
        {
            string key = word.ToLowerInvariant().Replace('’', '\'');
            if (Lexicon.TryGetValue(key, out string[] phones)) return phones;

            if (key.EndsWith("'s") && Lexicon.TryGetValue(key[..^2], out string[] possessive))
                return Append(possessive, EndsWithSibilant(possessive) ? new[] { "IH0", "Z" } : new[] { "Z" });
            if (key.EndsWith("s") && key.Length > 3 && Lexicon.TryGetValue(key[..^1], out string[] plural))
                return Append(plural, new[] { "Z" });

            return SpellingRules(key.Replace("'", string.Empty));
        }

        private static Dictionary<string, string[]> Lexicon
        {
            get
            {
                if (lexicon != null) return lexicon;
                TextAsset asset = Resources.Load<TextAsset>(LexiconResource);
                lexicon = asset == null ? new Dictionary<string, string[]>() : Parse(asset.text);
                if (asset == null) Debug.LogWarning("ENGLISH_LEXICON_MISSING: using spelling rules only.");
                return lexicon;
            }
        }

        private static Dictionary<string, string[]> Parse(string text)
        {
            Dictionary<string, string[]> entries = new(32000, StringComparer.Ordinal);
            foreach (string line in (text ?? string.Empty).Split('\n'))
            {
                if (line.Length == 0 || line[0] == '#') continue;
                string[] parts = line.TrimEnd('\r').Split(' ');
                if (parts.Length < 2) continue;
                string[] phones = new string[parts.Length - 1];
                Array.Copy(parts, 1, phones, 0, phones.Length);
                entries[parts[0]] = phones;
            }
            return entries;
        }

        private static bool IsWordCharacter(string source, int index, bool numeric)
        {
            char value = source[index];
            if (numeric) return char.IsDigit(value);
            if (char.IsLetter(value)) return true;
            // Apostrophes belong to contractions only when a letter follows ("don't", "Milo's").
            return (value == '\'' || value == '’') && index + 1 < source.Length && char.IsLetter(source[index + 1]);
        }

        private static string[] PronounceNumber(string digits)
        {
            if (!long.TryParse(digits, out long value) || digits.Length > 9)
            {
                List<string> spelled = new();
                foreach (char digit in digits) spelled.AddRange(Pronounce(Ones[digit - '0']));
                return spelled.ToArray();
            }

            List<string> phones = new();
            foreach (string word in NumberWords(value).Split(' ', StringSplitOptions.RemoveEmptyEntries))
                phones.AddRange(Pronounce(word));
            return phones.ToArray();
        }

        private static string NumberWords(long value)
        {
            if (value < 20) return Ones[value];
            if (value < 100) return Tens[value / 10] + (value % 10 == 0 ? string.Empty : " " + Ones[value % 10]);
            if (value < 1000) return Ones[value / 100] + " hundred" + (value % 100 == 0 ? string.Empty : " " + NumberWords(value % 100));
            if (value < 1_000_000) return NumberWords(value / 1000) + " thousand" + (value % 1000 == 0 ? string.Empty : " " + NumberWords(value % 1000));
            return NumberWords(value / 1_000_000) + " million" + (value % 1_000_000 == 0 ? string.Empty : " " + NumberWords(value % 1_000_000));
        }

        private static bool EndsWithSibilant(string[] phones)
        {
            string last = phones.Length == 0 ? string.Empty : phones[^1];
            return last is "S" or "Z" or "SH" or "ZH" or "CH" or "JH";
        }

        private static string[] Append(string[] first, string[] second)
        {
            string[] result = new string[first.Length + second.Length];
            first.CopyTo(result, 0);
            second.CopyTo(result, first.Length);
            return result;
        }

        /// <summary>Approximate letter-to-sound rules for names and words outside the lexicon.</summary>
        internal static string[] SpellingRules(string word)
        {
            List<string> phones = new();
            string w = word.ToLowerInvariant();
            bool stressed = false;

            // Silent initial clusters and a silent final e after a consonant.
            int begin = w.StartsWith("kn") || w.StartsWith("wr") || w.StartsWith("gn") || w.StartsWith("ps") ? 1 : 0;
            int end = w.Length;
            if (end > 2 && w[end - 1] == 'e' && !IsVowel(w[end - 2]) && HasVowel(w, begin, end - 1)) end--;

            for (int i = begin; i < end;)
            {
                char c = w[i];
                char n = i + 1 < end ? w[i + 1] : '\0';
                char n2 = i + 2 < end ? w[i + 2] : '\0';

                if (IsVowel(c) || (c == 'y' && i > begin))
                {
                    string vowel = ResolveVowel(c, n, n2, out int vowelLength);
                    phones.Add(vowel + (stressed ? "0" : "1"));
                    stressed = true;
                    i += vowelLength;
                    continue;
                }

                if (c == 't' && n == 'c' && n2 == 'h') { phones.Add("CH"); i += 3; continue; }
                if (c == 'd' && n == 'g' && n2 == 'e') { phones.Add("JH"); i += 2; continue; }
                if (c == 't' && n == 'i' && (n2 == 'o' || n2 == 'a')) { phones.Add("SH"); i += 2; continue; }
                if (c == 'g' && n == 'h') { if (i == begin) phones.Add("G"); i += 2; continue; }

                string pair = n == '\0' ? string.Empty : new string(new[] { c, n });
                string digraph = pair switch
                {
                    "ch" => "CH", "sh" => "SH", "th" => "TH", "ph" => "F", "ck" => "K",
                    "ng" => "NG", "wh" => "W", "qu" => "K W", "kn" => "N", "wr" => "R",
                    _ => null
                };
                if (digraph != null)
                {
                    phones.AddRange(digraph.Split(' '));
                    i += 2;
                    continue;
                }

                if (n == c) { i++; continue; } // Double consonants sound once.
                string consonant = c switch
                {
                    'b' => "B", 'd' => "D", 'f' => "F", 'h' => "HH", 'j' => "JH", 'k' => "K",
                    'l' => "L", 'm' => "M", 'n' => "N", 'p' => "P", 'r' => "R", 's' => "S",
                    't' => "T", 'v' => "V", 'w' => "W", 'x' => "K S", 'y' => "Y", 'z' => "Z",
                    'c' => n is 'e' or 'i' or 'y' ? "S" : "K",
                    'g' => n is 'e' or 'i' or 'y' ? "JH" : "G",
                    'q' => "K",
                    _ => null
                };
                if (consonant != null) phones.AddRange(consonant.Split(' '));
                i++;
            }
            return phones.ToArray();
        }

        private static string ResolveVowel(char c, char n, char n2, out int length)
        {
            length = 2;
            string pair = new(new[] { c, n });
            switch (pair)
            {
                case "ee": case "ea": case "ie": return "IY";
                case "oo": return "UW";
                case "ou": case "ow": return "AW";
                case "oa": return "OW";
                case "ai": case "ay": case "ei": case "ey": return "EY";
                case "oi": case "oy": return "OY";
                case "au": case "aw": return "AO";
                case "ew": case "ue": return "UW";
                case "ar": length = 1; return "AA"; // The r is spoken separately.
                case "er": case "ir": case "ur": return "ER";
                case "or": length = 1; return "AO";
            }

            length = 1;
            return c switch
            {
                'a' => "AE",
                'e' => "EH",
                'i' => "IH",
                'o' => "AA",
                'u' => "AH",
                _ => "IY"
            };
        }

        private static bool IsVowel(char value) => value is 'a' or 'e' or 'i' or 'o' or 'u';

        private static bool HasVowel(string word, int start, int end)
        {
            for (int i = start; i < end; i++) if (IsVowel(word[i])) return true;
            return false;
        }
    }
}
