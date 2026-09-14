// Soft clay toon shading for Milo. Colours come from vertex colours; the vertex alpha is a
// shading class written by tools/blender/build_milo_character.py:
//   1.0 skin, 0.75 cloth, 0.5 hair, 0.25 dark detail (pupils, mouth), 0.0 bright detail.
Shader "PersonalAssistant/MiloClayToon"
{
    Properties
    {
        _BaseColor ("Tint", Color) = (1, 1, 1, 1)
        _SkinShadow ("Skin Shadow Tint", Color) = (0.93, 0.62, 0.58, 1)
        _ClothShadow ("Cloth Shadow Tint", Color) = (0.66, 0.60, 0.70, 1)
        _RimStrength ("Rim Strength", Range(0, 1)) = 0.22
        _AmbientStrength ("Ambient Strength", Range(0, 2)) = 0.85
    }

    SubShader
    {
        Tags { "RenderType" = "Opaque" "RenderPipeline" = "UniversalPipeline" "Queue" = "Geometry" }

        Pass
        {
            Name "ClayForward"
            Tags { "LightMode" = "UniversalForward" }

            HLSLPROGRAM
            #pragma vertex Vertex
            #pragma fragment Fragment
            #pragma multi_compile_instancing
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Lighting.hlsl"

            CBUFFER_START(UnityPerMaterial)
                half4 _BaseColor;
                half4 _SkinShadow;
                half4 _ClothShadow;
                half _RimStrength;
                half _AmbientStrength;
            CBUFFER_END

            struct Attributes
            {
                float4 positionOS : POSITION;
                float3 normalOS : NORMAL;
                half4 color : COLOR;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float3 positionWS : TEXCOORD0;
                half3 normalWS : TEXCOORD1;
                half4 color : COLOR;
            };

            Varyings Vertex(Attributes input)
            {
                UNITY_SETUP_INSTANCE_ID(input);
                Varyings output;
                VertexPositionInputs position = GetVertexPositionInputs(input.positionOS.xyz);
                output.positionCS = position.positionCS;
                output.positionWS = position.positionWS;
                output.normalWS = TransformObjectToWorldNormal(input.normalOS);
                half3 rgb = input.color.rgb;
                #if !defined(UNITY_COLORSPACE_GAMMA)
                    rgb = SRGBToLinear(rgb); // FBX vertex colours are authored in sRGB.
                #endif
                output.color = half4(rgb * _BaseColor.rgb, input.color.a);
                return output;
            }

            half Band(half value, half center, half softness)
            {
                return smoothstep(center - softness, center + softness, value);
            }

            half4 Fragment(Varyings input) : SV_Target
            {
                half3 albedo = input.color.rgb;
                half shade = input.color.a;
                half skin = Band(shade, 0.875, 0.06);
                half cloth = Band(shade, 0.625, 0.06) * (1 - skin);
                half hair = Band(shade, 0.375, 0.06) * (1 - skin - cloth);
                half dark = Band(shade, 0.125, 0.06) * (1 - skin - cloth - hair);
                half bright = 1 - skin - cloth - hair - dark;

                half3 normal = normalize(input.normalWS);
                half3 view = normalize(GetWorldSpaceViewDir(input.positionWS));
                Light light = GetMainLight();
                half halfLambert = dot(normal, light.direction) * 0.5 + 0.5;

                // Two soft bands read as clay rather than cel paint.
                half lit = Band(halfLambert, 0.47, 0.10);
                half top = Band(halfLambert, 0.80, 0.08);
                half3 shadowTint = lerp(_ClothShadow.rgb, _SkinShadow.rgb, skin);
                shadowTint = lerp(shadowTint, half3(0.55, 0.55, 0.60), hair + dark);
                half3 diffuse = albedo * lerp(shadowTint * 0.72, half3(1, 1, 1), lit);
                diffuse += albedo * top * 0.08;
                diffuse *= light.color;

                half3 ambient = SampleSH(normal) * albedo * _AmbientStrength;

                // Broad, soft highlights: glossy hair and eyes, satin skin, matte cloth.
                half3 halfVector = normalize(light.direction + view);
                half nh = saturate(dot(normal, halfVector));
                half gloss = skin * 18 + cloth * 8 + hair * 70 + dark * 110 + bright * 40;
                half strength = skin * 0.06 + cloth * 0.025 + hair * 0.22 + dark * 0.45 + bright * 0.12;
                half specular = Band(pow(nh, gloss), 0.5, 0.25) * strength;

                half fresnel = pow(1 - saturate(dot(normal, view)), 3);
                half3 rim = fresnel * _RimStrength * lerp(half3(1, 0.92, 0.85), albedo + 0.25, 0.5) * (0.35 + 0.65 * lit);

                half3 color = diffuse + ambient + specular * light.color + rim * (1 - dark);
                // Eye whites, teeth and the status light stay clean and readable.
                color = lerp(color, albedo * (0.82 + 0.18 * lit) + specular, bright * 0.6);
                return half4(color, 1);
            }
            ENDHLSL
        }
    }
}
