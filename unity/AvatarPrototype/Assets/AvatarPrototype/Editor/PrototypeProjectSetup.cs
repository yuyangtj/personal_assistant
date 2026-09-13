using System.IO;
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

        [MenuItem("Personal Assistant/Create Prototype Scene")]
        public static void CreatePrototypeScene()
        {
            ConfigureRenderPipeline();

            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            scene.name = "AvatarPrototype";

            GameObject cameraObject = new("Main Camera");
            Camera camera = cameraObject.AddComponent<Camera>();
            cameraObject.tag = "MainCamera";
            cameraObject.transform.position = new Vector3(0f, 2.55f, -7.3f);
            cameraObject.transform.LookAt(new Vector3(0f, 2.05f, 0f));
            camera.fieldOfView = 37f;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.028f, 0.055f, 0.071f);
            camera.allowHDR = true;

            GameObject keyObject = new("Key Light");
            Light key = keyObject.AddComponent<Light>();
            key.type = LightType.Directional;
            key.color = new Color(1f, 0.88f, 0.77f);
            key.intensity = 1.8f;
            key.shadows = LightShadows.Soft;
            keyObject.transform.rotation = Quaternion.Euler(28f, -32f, 0f);

            GameObject fillObject = new("Fill Light");
            Light fill = fillObject.AddComponent<Light>();
            fill.type = LightType.Point;
            fill.color = new Color(0.21f, 0.72f, 0.88f);
            fill.intensity = 7f;
            fill.range = 8f;
            fillObject.transform.position = new Vector3(-3f, 3.5f, -2.2f);

            GameObject rimObject = new("Rim Light");
            Light rim = rimObject.AddComponent<Light>();
            rim.type = LightType.Point;
            rim.color = new Color(0.40f, 0.95f, 0.74f);
            rim.intensity = 6f;
            rim.range = 7f;
            rimObject.transform.position = new Vector3(2.8f, 3.8f, 1.2f);

            GameObject avatarObject = new("AvatarRuntime");
            avatarObject.AddComponent<ProceduralAvatarController>();
            avatarObject.AddComponent<PrototypeDemo>();

            RenderSettings.ambientMode = AmbientMode.Trilight;
            RenderSettings.ambientSkyColor = new Color(0.24f, 0.30f, 0.34f);
            RenderSettings.ambientEquatorColor = new Color(0.12f, 0.18f, 0.20f);
            RenderSettings.ambientGroundColor = new Color(0.035f, 0.045f, 0.05f);

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
            if (!File.Exists(ScenePath)) CreatePrototypeScene();
            Directory.CreateDirectory("Builds/Android");
            EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Android, BuildTarget.Android);
            BuildPlayerOptions options = new()
            {
                scenes = new[] { ScenePath },
                locationPathName = "Builds/Android/avatar-prototype.apk",
                target = BuildTarget.Android,
                options = BuildOptions.Development
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

            avatar.ApplyCommandJson("{\"mode\":\"Speaking\",\"emotion\":\"Excited\",\"intensity\":0.8}");
            if (avatar.Mode != AvatarMode.Speaking || avatar.Emotion != AvatarEmotion.Excited || Mathf.Abs(avatar.Intensity - 0.8f) > 0.001f)
                throw new BuildFailedException("Avatar JSON command contract failed validation.");

            if (GraphicsSettings.defaultRenderPipeline is not UniversalRenderPipelineAsset)
                throw new BuildFailedException("URP is not configured as the default render pipeline.");
            if (EditorBuildSettings.scenes.Length != 1 || EditorBuildSettings.scenes[0].path != ScenePath)
                throw new BuildFailedException("Prototype scene is not configured as the only build scene.");

            Debug.Log("PROTOTYPE_VALIDATION_PASSED: scene, URP, camera, runtime components, and JSON command contract are valid.");
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
            PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
            PlayerSettings.colorSpace = ColorSpace.Linear;
            Application.targetFrameRate = 60;
        }
    }
}
