using System.IO;
using System.Linq;
using PersonalAssistant.Avatar;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;

namespace PersonalAssistant.Avatar.Editor
{
    public static class PrototypeProjectSetup
    {
        private const string ScenePath = "Assets/AvatarPrototype/Scenes/AvatarPrototype.unity";
        private const string SettingsFolder = "Assets/AvatarPrototype/Settings";
        private const string CoolManRigPath = "Assets/AvatarPrototype/Resources/Character/CoolManRig.fbx";
        private const string CoolManFolder = "Assets/AvatarPrototype/Characters/CoolMan";
        private static readonly string[] FaceShapes =
        {
            "viseme_sil", "viseme_PP", "viseme_FF", "viseme_TH", "viseme_DD", "viseme_kk", "viseme_CH", "viseme_SS", "viseme_nn",
            "viseme_RR", "viseme_aa", "viseme_E", "viseme_ih", "viseme_oh", "viseme_ou", "jawOpen", "mouthSmile.L", "mouthSmile.R",
            "mouthFrown.L", "mouthFrown.R", "eyeBlink.L", "eyeBlink.R", "browInnerUp", "browOuterUp.L", "browOuterUp.R",
            "browDown.L", "browDown.R", "cheekPuff"
        };

        [System.Serializable]
        private sealed class MaterialManifest
        {
            public MaterialEntry[] materials;
        }

        [System.Serializable]
        private sealed class MaterialEntry
        {
            public string name;
            public string baseColor;
            public string normal;
            public float[] color;
            public bool alphaClip;
            public bool emission;
            public bool matte;
            public float smoothness = 0.35f;
        }

        /// <summary>
        /// Sets up the realistic character's import (legacy gesture clips, blendshapes) and builds
        /// URP Lit material assets from the Blender manifest, remapped onto the model.
        /// </summary>
        [MenuItem("Personal Assistant/Configure Cool Man Character")]
        public static void ConfigureCoolManAsset()
        {
            AssetDatabase.Refresh();
            string manifestPath = CoolManFolder + "/CoolManMaterials.json";
            if (!File.Exists(manifestPath)) throw new BuildFailedException($"Cool Man material manifest is missing: {manifestPath}");
            MaterialManifest manifest = JsonUtility.FromJson<MaterialManifest>(File.ReadAllText(manifestPath));
            Shader lit = Shader.Find("Universal Render Pipeline/Lit");
            if (lit == null) throw new BuildFailedException("URP Lit shader is unavailable.");

            string materialFolder = CoolManFolder + "/Materials";
            Directory.CreateDirectory(materialFolder);
            ModelImporter importer = AssetImporter.GetAtPath(CoolManRigPath) as ModelImporter;
            if (importer == null) throw new BuildFailedException($"Cool Man rig is missing: {CoolManRigPath}");

            foreach (MaterialEntry entry in manifest.materials)
            {
                string materialPath = $"{materialFolder}/{entry.name}.mat";
                Material material = AssetDatabase.LoadAssetAtPath<Material>(materialPath);
                if (material == null)
                {
                    material = new Material(lit);
                    AssetDatabase.CreateAsset(material, materialPath);
                }
                material.shader = lit;
                material.SetColor("_BaseColor", entry.color is { Length: 4 } ? new Color(entry.color[0], entry.color[1], entry.color[2], entry.color[3]) : Color.white);
                material.SetTexture("_BaseMap", LoadTexture(entry.baseColor, false, entry.alphaClip));
                material.SetFloat("_Smoothness", entry.smoothness);
                material.SetFloat("_Metallic", 0f);

                Texture2D normal = LoadTexture(entry.normal, true, false);
                material.SetTexture("_BumpMap", normal);
                SetKeyword(material, "_NORMALMAP", normal != null);

                material.SetFloat("_AlphaClip", entry.alphaClip ? 1f : 0f);
                material.SetFloat("_Cutoff", 0.5f);
                material.SetFloat("_Cull", entry.alphaClip ? 0f : 2f);
                SetKeyword(material, "_ALPHATEST_ON", entry.alphaClip);
                material.renderQueue = entry.alphaClip ? (int)RenderQueue.AlphaTest : (int)RenderQueue.Geometry;

                // Matte interiors must not mirror the bright studio environment.
                material.SetFloat("_SpecularHighlights", entry.matte ? 0f : 1f);
                material.SetFloat("_EnvironmentReflections", entry.matte ? 0f : 1f);
                SetKeyword(material, "_SPECULARHIGHLIGHTS_OFF", entry.matte);
                SetKeyword(material, "_ENVIRONMENTREFLECTIONS_OFF", entry.matte);
                SetKeyword(material, "_EMISSION", entry.emission);
                material.SetColor("_EmissionColor", entry.emission ? material.GetColor("_BaseColor") * 1.6f : Color.black);
                material.globalIlluminationFlags = entry.emission ? MaterialGlobalIlluminationFlags.RealtimeEmissive : MaterialGlobalIlluminationFlags.EmissiveIsBlack;
                EditorUtility.SetDirty(material);
                importer.AddRemap(new AssetImporter.SourceAssetIdentifier(typeof(Material), entry.name), material);
            }

            importer.animationType = ModelImporterAnimationType.Legacy;
            importer.importAnimation = true;
            importer.importBlendShapes = true;
            importer.importNormals = ModelImporterNormals.Import;
            importer.importBlendShapeNormals = ModelImporterNormals.Calculate;
            importer.materialImportMode = ModelImporterMaterialImportMode.ImportViaMaterialDescription;
            ModelImporterClipAnimation[] clips = importer.clipAnimations.Length > 0 ? importer.clipAnimations : importer.defaultClipAnimations;
            foreach (ModelImporterClipAnimation clip in clips)
            {
                clip.loopTime = false;
                clip.wrapMode = WrapMode.Once;
            }
            importer.clipAnimations = clips;
            AssetDatabase.SaveAssets();
            importer.SaveAndReimport();
            Debug.Log($"COOLMAN_ASSET_CONFIGURED: materials={manifest.materials.Length}, clips={string.Join(",", clips.Select(clip => clip.name))}");
        }

        private static Texture2D LoadTexture(string fileName, bool normalMap, bool alpha)
        {
            if (string.IsNullOrEmpty(fileName)) return null;
            string path = $"{CoolManFolder}/Textures/{fileName}";
            if (AssetImporter.GetAtPath(path) is TextureImporter textureImporter)
            {
                bool changed = false;
                TextureImporterType type = normalMap ? TextureImporterType.NormalMap : TextureImporterType.Default;
                if (textureImporter.textureType != type) { textureImporter.textureType = type; changed = true; }
                if (textureImporter.sRGBTexture == normalMap) { textureImporter.sRGBTexture = !normalMap; changed = true; }
                if (textureImporter.alphaIsTransparency != alpha) { textureImporter.alphaIsTransparency = alpha; changed = true; }
                if (textureImporter.maxTextureSize > 2048) { textureImporter.maxTextureSize = 2048; changed = true; }
                if (changed) textureImporter.SaveAndReimport();
            }
            Texture2D texture = AssetDatabase.LoadAssetAtPath<Texture2D>(path);
            if (texture == null) throw new BuildFailedException($"Cool Man texture is missing: {path}");
            return texture;
        }

        private static void SetKeyword(Material material, string keyword, bool enabled)
        {
            if (enabled) material.EnableKeyword(keyword);
            else material.DisableKeyword(keyword);
        }

        [MenuItem("Personal Assistant/Create Prototype Scene")]
        public static void CreatePrototypeScene()
        {
            ConfigureRenderPipeline();
            if (File.Exists(CoolManRigPath)) ConfigureCoolManAsset();

            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            scene.name = "AvatarPrototype";

            GameObject cameraObject = new("Main Camera");
            Camera camera = cameraObject.AddComponent<Camera>();
            cameraObject.tag = "MainCamera";
            cameraObject.transform.position = new Vector3(0f, 2.2f, -7.3f);
            cameraObject.transform.LookAt(new Vector3(0f, 1.9f, 0f));
            camera.fieldOfView = 46f;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.95f, 0.89f, 0.83f);
            camera.allowHDR = true;

            GameObject keyObject = new("Key Light");
            Light key = keyObject.AddComponent<Light>();
            key.type = LightType.Directional;
            key.color = new Color(1f, 0.95f, 0.89f);
            key.intensity = 1.15f;
            key.shadows = LightShadows.Soft;
            key.shadowStrength = 0.55f;
            keyObject.transform.rotation = Quaternion.Euler(32f, 28f, 0f);

            GameObject avatarObject = new("AvatarRuntime");
            avatarObject.AddComponent<ProceduralAvatarController>();
            avatarObject.AddComponent<PrototypeDemo>();

            // Warm cream studio: soft sky fill and a gentle bounce from the floor.
            RenderSettings.ambientMode = AmbientMode.Trilight;
            RenderSettings.ambientSkyColor = new Color(0.62f, 0.60f, 0.62f);
            RenderSettings.ambientEquatorColor = new Color(0.50f, 0.44f, 0.40f);
            RenderSettings.ambientGroundColor = new Color(0.36f, 0.30f, 0.27f);

            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));
            EditorSceneManager.SaveScene(scene, ScenePath);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
            ConfigurePlayer();
            AssetDatabase.SaveAssets();
            Debug.Log($"Prototype scene created at {ScenePath}");
        }

        [MenuItem("Personal Assistant/Build Android APK")]
        public static void BuildAndroid()
        {
            BuildAndroidWithOptions(BuildOptions.Development);
        }

        [MenuItem("Personal Assistant/Build Android Release APK")]
        public static void BuildAndroidRelease()
        {
            BuildAndroidWithOptions(BuildOptions.None);
        }

        [MenuItem("Personal Assistant/Export Android Library Project")]
        public static void ExportAndroidLibrary()
        {
            if (!File.Exists(ScenePath)) CreatePrototypeScene();
            ConfigureRenderPipeline();
            ConfigurePlayer();
            Directory.CreateDirectory("Builds");
            EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Android, BuildTarget.Android);

            bool previousExportSetting = EditorUserBuildSettings.exportAsGoogleAndroidProject;
            AndroidApplicationEntry previousEntry = PlayerSettings.Android.applicationEntry;
            try
            {
                EditorUserBuildSettings.exportAsGoogleAndroidProject = true;
                PlayerSettings.Android.applicationEntry = AndroidApplicationEntry.Activity;
                BuildPlayerOptions options = new()
                {
                    scenes = new[] { ScenePath },
                    locationPathName = "Builds/AndroidExport",
                    target = BuildTarget.Android,
                    options = BuildOptions.None
                };
                BuildReport report = BuildPipeline.BuildPlayer(options);
                if (report.summary.result != BuildResult.Succeeded)
                    throw new BuildFailedException($"Android library export failed: {report.summary.result}");
                Debug.Log($"ANDROID_LIBRARY_EXPORT_PASSED: {Path.GetFullPath(report.summary.outputPath)}");
            }
            finally
            {
                PlayerSettings.Android.applicationEntry = previousEntry;
                EditorUserBuildSettings.exportAsGoogleAndroidProject = previousExportSetting;
                AssetDatabase.SaveAssets();
            }
        }

        private static void BuildAndroidWithOptions(BuildOptions buildOptions)
        {
            if (!File.Exists(ScenePath)) CreatePrototypeScene();
            ConfigureRenderPipeline();
            ConfigurePlayer();
            Directory.CreateDirectory("Builds/Android");
            EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Android, BuildTarget.Android);
            BuildPlayerOptions options = new()
            {
                scenes = new[] { ScenePath },
                locationPathName = "Builds/Android/avatar-prototype.apk",
                target = BuildTarget.Android,
                options = buildOptions
            };
            BuildReport report = BuildPipeline.BuildPlayer(options);
            if (report.summary.result != BuildResult.Succeeded)
                throw new BuildFailedException($"Android build failed: {report.summary.result}");
            Debug.Log($"Android APK built: {report.summary.outputPath} ({report.summary.totalSize} bytes)");
        }

        public static void CreateAndBuildAndroid()
        {
            CreatePrototypeScene();
            BuildAndroid();
        }

        public static void ValidatePrototype()
        {
            Scene scene = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
            if (!scene.IsValid()) throw new BuildFailedException("Prototype scene could not be loaded.");
            if (Camera.main == null) throw new BuildFailedException("Prototype scene has no tagged main camera.");

            ProceduralAvatarController avatar = Object.FindFirstObjectByType<ProceduralAvatarController>();
            PrototypeDemo demo = Object.FindFirstObjectByType<PrototypeDemo>();
            if (avatar == null || demo == null) throw new BuildFailedException("Prototype runtime components are missing.");
            avatar.BuildForPreview();
            if (avatar.ActiveProfile == null) throw new BuildFailedException("No rigged character could be built.");
            Debug.Log($"RIGGED_AVATAR_ACTIVE profile={avatar.ActiveProfile.Character}");

            avatar.ApplyCommandJson("{\"mode\":\"Speaking\",\"emotion\":\"Excited\",\"intensity\":0.8}");
            if (avatar.Mode != AvatarMode.Speaking || avatar.Emotion != AvatarEmotion.Excited || Mathf.Abs(avatar.Intensity - 0.8f) > 0.001f)
                throw new BuildFailedException("Avatar JSON command contract failed validation.");

            var speechCues = EnglishVisemePlanner.Build("Milo thought five cheerful people would listen.", out float speechDuration);
            string[] expectedSpeechVisemes = { "PP", "TH", "FF", "CH", "RR", "nn" };
            foreach (string viseme in expectedSpeechVisemes)
                if (!speechCues.Any(cue => cue.Name == viseme)) throw new BuildFailedException($"English viseme planner did not produce {viseme}.");
            if (speechDuration <= 1f) throw new BuildFailedException("English viseme planner produced an invalid timeline.");
            ValidateSpeech();

            const string responseJson = "{\"responseId\":\"validation-1\",\"text\":\"The response bridge is ready.\",\"emotion\":\"Warm\",\"intensity\":0.7}";
            avatar.ApplyAssistantResponseJson(responseJson);
            if (avatar.Mode != AvatarMode.Speaking || avatar.ActiveSpeechText != "The response bridge is ready." || avatar.LastAssistantResponseId != "validation-1")
                throw new BuildFailedException("Assistant response contract failed validation.");
            avatar.ApplyAssistantResponseJson("{\"responseId\":\"validation-1\",\"text\":\"Duplicate must not replace this.\"}");
            if (avatar.ActiveSpeechText != "The response bridge is ready.")
                throw new BuildFailedException("Assistant response duplicate protection failed validation.");

            if (GraphicsSettings.defaultRenderPipeline is not UniversalRenderPipelineAsset)
                throw new BuildFailedException("URP is not configured as the default render pipeline.");
            if (EditorBuildSettings.scenes.Length != 1 || EditorBuildSettings.scenes[0].path != ScenePath)
                throw new BuildFailedException("Prototype scene is not configured as the only build scene.");

            Debug.Log($"PROTOTYPE_VALIDATION_PASSED: scene, URP, command and assistant-response contracts, duplicate protection, and English viseme planner ({speechCues.Count} cues, {speechDuration:F2}s) are valid.");
        }

        /// <summary>Checks pronunciation, TTS word fitting, and lip closure; run with -executeMethod.</summary>
        public static void ValidateSpeech()
        {
            if (!EnglishPronouncer.HasLexicon) throw new BuildFailedException("English lexicon Resources/Speech/en_lexicon is missing.");

            static string[] Visemes(string text) => EnglishVisemePlanner.Build(text, out _).Where(cue => cue.Name != "sil").Select(cue => cue.Name).ToArray();
            void Expect(bool condition, string message)
            {
                if (!condition) throw new BuildFailedException($"Speech validation failed: {message}");
            }

            string[] phone = Visemes("phone");
            Expect(phone.Length > 0 && phone[0] == "FF" && !phone.Contains("PP") && phone.Contains("oh") && phone.Contains("nn"), $"'phone' => {string.Join(" ", phone)}");
            Expect(!Visemes("knight").Contains("kk"), $"'knight' => {string.Join(" ", Visemes("knight"))}");
            Expect(Visemes("the")[0] == "TH", "'the' should start with TH");
            Expect(Visemes("Milo")[0] == "PP", "'Milo' should start with PP");
            Expect(Visemes("Zorbexity").Length >= 6, "unknown words need spelling-rule fallback");
            Expect(Visemes("It's 42").Contains("FF"), "numbers should be spoken as words ('forty')");

            // Word timings from TTS pin every sound of a word to its measured audio window.
            const string timedText = "Hello there, Milo";
            TtsWordTiming[] timings = { new(0, 5, 0.10f), new(6, 11, 0.62f), new(13, 17, 1.40f) };
            var timed = EnglishVisemePlanner.Build(timedText, timings, 2.1f);
            foreach (var cue in timed.Where(cue => cue.Name != "sil"))
            {
                float windowStart = cue.CharacterIndex >= 13 ? 1.40f : cue.CharacterIndex >= 6 ? 0.62f : 0.10f;
                float windowEnd = cue.CharacterIndex >= 13 ? 2.1f : cue.CharacterIndex >= 6 ? 1.40f : 0.62f;
                Expect(cue.Time >= windowStart - 0.001f && cue.End <= windowEnd + 0.001f, $"cue {cue.Name}@{cue.Time:F3} escapes word window {windowStart:F2}-{windowEnd:F2}");
            }
            Expect(timed.First(cue => cue.CharacterIndex == 13 && cue.Name != "sil").Name == "PP" &&
                   Mathf.Abs(timed.First(cue => cue.CharacterIndex == 13).Time - 1.40f) < 0.001f, "'Milo' must start with PP at its TTS frame");

            // Every lip closure must visibly seal at its midpoint despite coarticulation.
            const string closureText = "My mom bought five big bubbles from Milo.";
            var closureCues = EnglishVisemePlanner.Build(closureText, out _);
            VisemeMixer mixer = new();
            int closures = 0;
            foreach (var cue in closureCues.Where(cue => cue.Name is "PP" or "FF"))
            {
                mixer.Evaluate(closureCues, cue.Time + cue.Duration * 0.5f, 0.8f);
                float weight = mixer.Weights[VisemeMixer.IndexOf(cue.Name)];
                Expect(weight >= 0.85f, $"{cue.Name} at {cue.Time:F3}s only reaches {weight:F2}");
                closures++;
            }
            Expect(closures >= 9, $"expected at least 9 lip closures, found {closures}");

            mixer.Evaluate(closureCues, closureCues.First(cue => cue.Name == "aa").Time + 0.03f, 1f);
            Expect(mixer.Jaw > 0.4f, $"open vowels must drop the jaw (jaw={mixer.Jaw:F2})");

            Debug.Log($"SPEECH_VALIDATION_PASSED: phone=[{string.Join(" ", phone)}], closures={closures}, timedCues={timed.Count}");
        }

        public static void ValidateRiggedAsset()
        {
            ValidateCharacterAsset(AvatarCharacterProfile.CoolMan, "Assistant_Face", new[] { "Hips", "Chest", "Neck", "Head", "Jaw", "Eye.L", "Eye.R", "UpperArm.L", "UpperArm.R", "LowerArm.L", "LowerArm.R", "Hand.L", "Hand.R", "Assistant_Body", "Status_Orb" });
            ValidateCharacterAsset(AvatarCharacterProfile.Milo, "Milo_Face", new[] { "Root", "Hips", "Chest", "Neck", "Head", "Jaw", "Eye.L", "Eye.R", "UpperArm.L", "UpperArm.R", "Hand.L", "Hand.R", "Milo_Body", "Milo_Hair", "Status_Orb" });
        }

        private static void ValidateCharacterAsset(AvatarCharacterProfile profile, string faceName, string[] requiredNodes)
        {
            string rigPath = $"Assets/AvatarPrototype/Resources/{profile.ResourcePath}.fbx";
            GameObject rig = AssetDatabase.LoadAssetAtPath<GameObject>(rigPath);
            if (rig == null) throw new BuildFailedException($"{profile.Character} rig is missing at {rigPath}.");

            string[] hierarchyNames = rig.GetComponentsInChildren<Transform>(true).Select(item => item.name).ToArray();
            foreach (string node in requiredNodes)
                if (!hierarchyNames.Contains(node)) throw new BuildFailedException($"{profile.Character} node is missing: {node}");

            SkinnedMeshRenderer[] skinned = rig.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            if (skinned.Length > 3) throw new BuildFailedException($"{profile.Character} has {skinned.Length} skinned renderers; the mobile budget is 3.");
            SkinnedMeshRenderer face = skinned.FirstOrDefault(renderer => renderer.name == faceName);
            if (face == null || face.sharedMesh == null) throw new BuildFailedException($"{profile.Character} face mesh is missing.");
            Mesh faceMesh = face.sharedMesh;
            string[] blendShapes = Enumerable.Range(0, faceMesh.blendShapeCount).Select(faceMesh.GetBlendShapeName).ToArray();
            foreach (string shape in FaceShapes)
                if (!blendShapes.Contains(shape)) throw new BuildFailedException($"{profile.Character} blendshape is missing: {shape}");

            int triangles = rig.GetComponentsInChildren<Renderer>(true)
                .Select(renderer => renderer is SkinnedMeshRenderer s ? s.sharedMesh : renderer.GetComponent<MeshFilter>()?.sharedMesh)
                .Where(mesh => mesh != null)
                .Sum(mesh => (int)Enumerable.Range(0, mesh.subMeshCount).Sum(index => (long)mesh.GetIndexCount(index)) / 3);
            if (triangles > 50000) throw new BuildFailedException($"{profile.Character} has {triangles} triangles; the mobile budget is 50,000.");

            string detail = string.Empty;
            if (profile.Character == AvatarCharacter.CoolMan)
            {
                string[] clips = AssetDatabase.LoadAllAssetsAtPath(rigPath).OfType<AnimationClip>()
                    .Where(clip => !clip.name.StartsWith("__preview__")).Select(clip => clip.name).ToArray();
                foreach (string clip in new[] { profile.GreetingClip, profile.SuccessClip })
                    if (!clips.Any(name => name.EndsWith(clip))) throw new BuildFailedException($"Cool Man clip is missing: {clip} (found {string.Join(", ", clips)})");

                foreach (Renderer renderer in rig.GetComponentsInChildren<Renderer>(true))
                foreach (Material material in renderer.sharedMaterials)
                    if (material == null || material.shader == null || !material.shader.name.StartsWith("Universal Render Pipeline"))
                        throw new BuildFailedException($"Cool Man renderer {renderer.name} has a missing or non-URP material.");

                GameObject probe = (GameObject)PrefabUtility.InstantiatePrefab(rig);
                Bounds bounds = probe.GetComponentsInChildren<SkinnedMeshRenderer>().Select(r => r.bounds).Aggregate((a, b) => { a.Encapsulate(b); return a; });
                Object.DestroyImmediate(probe);
                if (bounds.size.y < 1.6f || bounds.size.y > 2.0f)
                    throw new BuildFailedException($"Cool Man height {bounds.size.y:F2} m is outside 1.6-2.0 m.");
                detail = $", height={bounds.size.y:F2}m, clips={string.Join("|", clips)}";
            }

            Debug.Log($"RIGGED_ASSET_VALIDATION_PASSED: profile={profile.Character}, nodes={hierarchyNames.Length}, skinned={skinned.Length}, triangles={triangles}, faceBlendShapes={faceMesh.blendShapeCount}{detail}.");
        }

        public static void CapturePrototypeFrame()
        {
            EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
            ProceduralAvatarController avatar = Object.FindFirstObjectByType<ProceduralAvatarController>();
            Camera camera = Camera.main;
            if (avatar == null || camera == null) throw new BuildFailedException("Prototype scene is incomplete.");

            foreach (AvatarCharacter character in new[] { AvatarCharacter.CoolMan, AvatarCharacter.Milo })
            {
                avatar.SetCharacter(character, remember: false);
                avatar.BuildForPreview();
                if (avatar.ActiveProfile == null || avatar.ActiveProfile.Character != character)
                    throw new BuildFailedException($"{character} did not become the active character.");
                string fileName = character == AvatarCharacter.CoolMan ? "prototype-frame-coolman.png" : "prototype-frame-v3-clay.png";
                string outputPath = Path.GetFullPath(Path.Combine(Application.dataPath, "../../../design", fileName));
                RenderFrame(camera, outputPath);
                Debug.Log($"PROTOTYPE_FRAME_CAPTURED: {character} {outputPath}");
            }
        }

        private static void RenderFrame(Camera camera, string outputPath)
        {
            const int width = 1080;
            const int height = 2404;
            RenderTexture renderTexture = new(width, height, 24, RenderTextureFormat.ARGB32);
            Texture2D frame = new(width, height, TextureFormat.RGB24, false);
            RenderTexture previous = RenderTexture.active;
            // Batch-mode renders otherwise show placeholder output while URP Lit variants compile.
            ShaderUtil.allowAsyncCompilation = false;
            camera.targetTexture = renderTexture;
            camera.Render();
            camera.Render();
            RenderTexture.active = renderTexture;
            frame.ReadPixels(new Rect(0, 0, width, height), 0, 0);
            frame.Apply();
            File.WriteAllBytes(outputPath, frame.EncodeToPNG());
            camera.targetTexture = null;
            RenderTexture.active = previous;
            Object.DestroyImmediate(frame);
            Object.DestroyImmediate(renderTexture);
        }

        private static void ConfigureRenderPipeline()
        {
            Directory.CreateDirectory(SettingsFolder);
            const string rendererPath = SettingsFolder + "/MobileRenderer.asset";
            const string pipelinePath = SettingsFolder + "/MobileURP.asset";

            UniversalRendererData renderer = AssetDatabase.LoadAssetAtPath<UniversalRendererData>(rendererPath);
            if (renderer == null)
            {
                renderer = ScriptableObject.CreateInstance<UniversalRendererData>();
                AssetDatabase.CreateAsset(renderer, rendererPath);
            }

            UniversalRenderPipelineAsset pipeline = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>(pipelinePath);
            if (pipeline == null)
            {
                pipeline = UniversalRenderPipelineAsset.Create(renderer);
                pipeline.renderScale = 1f;
                pipeline.msaaSampleCount = 4;
                pipeline.supportsHDR = true;
                pipeline.shadowDistance = 18f;
                AssetDatabase.CreateAsset(pipeline, pipelinePath);
            }

            GraphicsSettings.defaultRenderPipeline = pipeline;
            QualitySettings.renderPipeline = pipeline;
            PreserveRuntimeShader();
        }

        private static void PreserveRuntimeShader()
        {
            PreserveRuntimeShader("Universal Render Pipeline/Lit");
            PreserveRuntimeShader("PersonalAssistant/MiloClayToon");
        }

        private static void PreserveRuntimeShader(string shaderName)
        {
            Shader shader = Shader.Find(shaderName);
            if (shader == null) throw new BuildFailedException($"{shaderName} shader is unavailable.");

            Object[] settingsAssets = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/GraphicsSettings.asset");
            if (settingsAssets.Length == 0) throw new BuildFailedException("Graphics settings asset is unavailable.");
            SerializedObject settings = new(settingsAssets[0]);
            SerializedProperty shaders = settings.FindProperty("m_AlwaysIncludedShaders");
            if (shaders == null) throw new BuildFailedException("Always Included Shaders setting is unavailable.");

            for (int i = 0; i < shaders.arraySize; i++)
            {
                if (shaders.GetArrayElementAtIndex(i).objectReferenceValue == shader) return;
            }

            int index = shaders.arraySize;
            shaders.InsertArrayElementAtIndex(index);
            shaders.GetArrayElementAtIndex(index).objectReferenceValue = shader;
            settings.ApplyModifiedPropertiesWithoutUndo();
            AssetDatabase.SaveAssets();
        }

        private static void ConfigurePlayer()
        {
            PlayerSettings.companyName = "Personal Assistant";
            PlayerSettings.productName = "Avatar Prototype";
            PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Android, "com.personalassistant.avatar.prototype");
            PlayerSettings.defaultInterfaceOrientation = UIOrientation.Portrait;
            PlayerSettings.Android.minSdkVersion = AndroidSdkVersions.AndroidApiLevel26;
            PlayerSettings.Android.targetSdkVersion = AndroidSdkVersions.AndroidApiLevelAuto;
            PlayerSettings.SetScriptingBackend(NamedBuildTarget.Android, ScriptingImplementation.IL2CPP);
            PlayerSettings.SetManagedStrippingLevel(NamedBuildTarget.Android, ManagedStrippingLevel.Low);
            PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
            PlayerSettings.colorSpace = ColorSpace.Linear;
            Application.targetFrameRate = 60;
        }
    }
}
