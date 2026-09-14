using System.Collections.Generic;
using UnityEngine;

namespace PersonalAssistant.Avatar
{
    /// <summary>
    /// Samples a viseme timeline into blended weights. Neighbouring sounds overlap
    /// (coarticulation), lip closures always read clearly, and loudness drives the jaw.
    /// </summary>
    public sealed class VisemeMixer
    {
        public static readonly string[] Visemes = { "sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "ih", "oh", "ou" };

        private const float Anticipation = 0.055f;
        private const float Release = 0.075f;
        private const float MaximumCueDuration = 0.6f;
        private const float RestOpen = 0.035f;
        private const float RestWidth = 0.72f;

        // Per-viseme mouth openness, width, and jaw drop for the procedural fallback and jaw.
        private static readonly float[] OpenByViseme = { 0.035f, 0.02f, 0.08f, 0.12f, 0.09f, 0.15f, 0.11f, 0.07f, 0.09f, 0.14f, 0.36f, 0.22f, 0.17f, 0.30f, 0.21f };
        private static readonly float[] WidthByViseme = { 0.72f, 0.62f, 0.88f, 0.78f, 0.80f, 0.82f, 0.92f, 1.14f, 0.76f, 0.74f, 0.96f, 1.10f, 0.90f, 0.80f, 0.66f };
        private static readonly float[] JawByViseme = { 0f, 0f, 0.10f, 0.18f, 0.22f, 0.32f, 0.20f, 0.10f, 0.20f, 0.28f, 1.00f, 0.62f, 0.42f, 0.78f, 0.45f };

        public readonly float[] Weights = new float[Visemes.Length];
        public float Jaw { get; private set; }
        public float Open { get; private set; } = RestOpen;
        public float Width { get; private set; } = RestWidth;
        public string Dominant { get; private set; } = "sil";

        public static int IndexOf(string viseme)
        {
            for (int i = 0; i < Visemes.Length; i++)
                if (Visemes[i] == viseme) return i;
            return 0;
        }

        public void Clear()
        {
            System.Array.Clear(Weights, 0, Weights.Length);
            Jaw = 0f;
            Open = RestOpen;
            Width = RestWidth;
            Dominant = "sil";
        }

        /// <param name="loudness">Normalized audio loudness at <paramref name="time"/>, or a negative value when unknown.</param>
        public void Evaluate(IReadOnlyList<EnglishVisemeCue> cues, float time, float loudness)
        {
            Clear();
            if (cues == null || cues.Count == 0) return;

            for (int i = FirstRelevantCue(cues, time); i < cues.Count; i++)
            {
                EnglishVisemeCue cue = cues[i];
                if (cue.Time - Anticipation > time) break;
                if (cue.Name == "sil") continue;
                float weight = CueWeight(cue, time);
                int index = IndexOf(cue.Name);
                if (weight > Weights[index]) Weights[index] = weight;
            }

            // Closures (p/b/m, f/v, th) must visibly seal even when they are short.
            // Adjacent closures ("five big") hand over: the strongest keeps full weight.
            int strongestClosure = 1;
            for (int i = 2; i <= 3; i++) if (Weights[i] > Weights[strongestClosure]) strongestClosure = i;
            float closure = Weights[strongestClosure];
            for (int i = 1; i <= 3; i++)
                if (i != strongestClosure) Weights[i] *= 1f - 0.9f * closure;

            float openSum = 0f;
            for (int i = 4; i < Weights.Length; i++) openSum += Weights[i];
            float openScale = (openSum > 1f ? 1f / openSum : 1f) * (1f - 0.9f * closure);
            for (int i = 4; i < Weights.Length; i++) Weights[i] *= openScale;

            float total = 0f;
            float open = 0f;
            float width = 0f;
            float jaw = 0f;
            float strongest = 0.15f;
            for (int i = 1; i < Weights.Length; i++)
            {
                float weight = Weights[i];
                total += weight;
                open += weight * OpenByViseme[i];
                width += weight * WidthByViseme[i];
                jaw += weight * JawByViseme[i];
                if (weight > strongest)
                {
                    strongest = weight;
                    Dominant = Visemes[i];
                }
            }

            float rest = Mathf.Clamp01(1f - total);
            Open = open + rest * RestOpen;
            Width = width + rest * RestWidth;
            float loudnessGain = loudness < 0f ? 0.85f : Mathf.Lerp(0.55f, 1.15f, Mathf.Clamp01(loudness));
            Jaw = Mathf.Clamp01(jaw * loudnessGain);
        }

        private static float CueWeight(EnglishVisemeCue cue, float time)
        {
            float attack = Mathf.Clamp(cue.Duration * 0.8f, 0.03f, Anticipation);
            if (time < cue.Time - attack || time > cue.End + Release) return 0f;
            float weight;
            if (time < cue.Time) weight = Smooth((time - (cue.Time - attack)) / attack);
            else if (time <= cue.End) weight = 1f;
            else weight = Smooth(1f - (time - cue.End) / Release);
            return weight * cue.Strength;
        }

        private static int FirstRelevantCue(IReadOnlyList<EnglishVisemeCue> cues, float time)
        {
            float earliest = time - MaximumCueDuration - Release;
            int low = 0;
            int high = cues.Count;
            while (low < high)
            {
                int middle = (low + high) / 2;
                if (cues[middle].Time < earliest) low = middle + 1;
                else high = middle;
            }
            return low;
        }

        private static float Smooth(float value)
        {
            value = Mathf.Clamp01(value);
            return value * value * (3f - 2f * value);
        }
    }
}
