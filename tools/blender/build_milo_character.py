from pathlib import Path
import math

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[2]
ART_DIR = ROOT / "unity" / "AvatarPrototype" / "Assets" / "AvatarPrototype" / "Resources" / "Character"
RENDER_DIR = ROOT / "design" / "renders"
BLEND_PATH = ART_DIR / "MiloRig.blend"
FBX_PATH = ART_DIR / "MiloRig.fbx"
PREVIEW_PATH = RENDER_DIR / "rigged-character-v1.png"


def reset_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.curves, bpy.data.armatures, bpy.data.materials):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def material(name, color, roughness=0.42, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    return mat


def finish_mesh(obj, mat, bone=None):
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    obj.data.materials.append(mat)
    if bone:
        parent_to_bone(obj, bone)
    CHARACTER_OBJECTS.append(obj)
    return obj


def ellipsoid(name, location, scale, mat, bone=None, segments=48, rings=32):
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=segments,
        ring_count=rings,
        location=location,
    )
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish_mesh(obj, mat, bone)


def rounded_box(name, location, scale, radius, mat, bone=None):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bevel = obj.modifiers.new("Soft silhouette", "BEVEL")
    bevel.width = radius
    bevel.segments = 6
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    return finish_mesh(obj, mat, bone)


def torus(name, location, major_radius, minor_radius, mat, bone=None, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(
        major_radius=major_radius,
        minor_radius=minor_radius,
        major_segments=48,
        minor_segments=12,
        location=location,
        rotation=rotation,
    )
    obj = bpy.context.object
    obj.name = name
    return finish_mesh(obj, mat, bone)


def tube(name, points, radius, mat, bone=None):
    curve = bpy.data.curves.new(name + "Curve", "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 3
    curve.bevel_depth = radius
    curve.bevel_resolution = 5
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for point, coordinate in zip(spline.bezier_points, points):
        point.co = coordinate
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.convert(target="MESH")
    obj = bpy.context.object
    return finish_mesh(obj, mat, bone)


def create_bone(armature, name, head, tail, parent=None):
    bone = armature.edit_bones.new(name)
    bone.head = head
    bone.tail = tail
    if parent:
        bone.parent = armature.edit_bones[parent]
    return bone


def create_rig():
    armature_data = bpy.data.armatures.new("MiloRigArmature")
    armature = bpy.data.objects.new("MiloRig", armature_data)
    bpy.context.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    create_bone(armature_data, "Root", (0, 0, 0), (0, 0, 0.25))
    create_bone(armature_data, "Hips", (0, 0, 0.72), (0, 0, 1.05), "Root")
    create_bone(armature_data, "Spine", (0, 0, 1.05), (0, 0, 1.48), "Hips")
    create_bone(armature_data, "Chest", (0, 0, 1.48), (0, 0, 1.92), "Spine")
    create_bone(armature_data, "Neck", (0, 0, 1.92), (0, 0, 2.15), "Chest")
    create_bone(armature_data, "Head", (0, 0, 2.15), (0, 0, 3.25), "Neck")

    create_bone(armature_data, "UpperArm.L", (-0.53, 0, 1.76), (-0.72, 0, 1.42), "Chest")
    create_bone(armature_data, "LowerArm.L", (-0.72, 0, 1.42), (-0.76, 0, 1.12), "UpperArm.L")
    create_bone(armature_data, "Hand.L", (-0.76, 0, 1.12), (-0.76, 0, 0.91), "LowerArm.L")
    create_bone(armature_data, "UpperArm.R", (0.53, 0, 1.76), (0.72, 0, 1.42), "Chest")
    create_bone(armature_data, "LowerArm.R", (0.72, 0, 1.42), (0.76, 0, 1.12), "UpperArm.R")
    create_bone(armature_data, "Hand.R", (0.76, 0, 1.12), (0.76, 0, 0.91), "LowerArm.R")

    create_bone(armature_data, "UpperLeg.L", (-0.23, 0, 0.76), (-0.23, 0, 0.48), "Hips")
    create_bone(armature_data, "LowerLeg.L", (-0.23, 0, 0.48), (-0.23, 0, 0.23), "UpperLeg.L")
    create_bone(armature_data, "Foot.L", (-0.23, 0, 0.23), (-0.23, -0.30, 0.12), "LowerLeg.L")
    create_bone(armature_data, "UpperLeg.R", (0.23, 0, 0.76), (0.23, 0, 0.48), "Hips")
    create_bone(armature_data, "LowerLeg.R", (0.23, 0, 0.48), (0.23, 0, 0.23), "UpperLeg.R")
    create_bone(armature_data, "Foot.R", (0.23, 0, 0.23), (0.23, -0.30, 0.12), "LowerLeg.R")

    bpy.ops.object.mode_set(mode="OBJECT")
    armature.show_in_front = True
    CHARACTER_OBJECTS.append(armature)
    return armature


def parent_to_bone(obj, bone_name):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    group = obj.vertex_groups.new(name=bone_name)
    group.add(range(len(obj.data.vertices)), 1.0, "REPLACE")
    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = RIG
    obj.parent = RIG
    obj.matrix_parent_inverse = RIG.matrix_world.inverted()


def create_shape_keys(mouth):
    basis = mouth.shape_key_add(name="Basis")
    shapes = {
        "viseme_sil": (1.00, 0.30, 0.30),
        "viseme_PP": (0.88, 0.28, 0.18),
        "viseme_FF": (1.10, 0.34, 0.34),
        "viseme_TH": (0.92, 0.40, 0.62),
        "viseme_DD": (1.05, 0.38, 0.55),
        "viseme_kk": (0.94, 0.42, 0.78),
        "viseme_CH": (0.78, 0.46, 0.72),
        "viseme_SS": (1.22, 0.34, 0.36),
        "viseme_nn": (1.05, 0.36, 0.42),
        "viseme_RR": (0.82, 0.43, 0.58),
        "viseme_aa": (1.08, 0.48, 1.65),
        "viseme_E": (1.34, 0.38, 0.68),
        "viseme_ih": (1.18, 0.36, 0.52),
        "viseme_oh": (0.72, 0.46, 1.30),
        "viseme_ou": (0.58, 0.52, 0.92),
        "expression_smile": (1.28, 0.36, 0.56),
        "expression_concerned": (0.94, 0.34, 0.42),
    }
    for name, (x_scale, y_scale, z_scale) in shapes.items():
        key = mouth.shape_key_add(name=name)
        key.value = 0.0
        for source, target in zip(basis.data, key.data):
            target.co.x = source.co.x * x_scale
            target.co.y = source.co.y * y_scale
            target.co.z = source.co.z * z_scale


def build_character():
    # Palette keeps the requested broad direction while retaining Milo's own identity.
    skin = material("Skin", (1.00, 0.55, 0.42), 0.48)
    skin_soft = material("SkinSoft", (1.00, 0.47, 0.39), 0.50)
    blush = material("Blush", (1.00, 0.34, 0.38), 0.52)
    hair = material("Hair", (0.028, 0.018, 0.025), 0.25)
    shirt = material("SweatshirtCoral", (0.78, 0.025, 0.045), 0.48)
    shirt_dark = material("SweatshirtRib", (0.52, 0.012, 0.025), 0.52)
    shorts = material("ShortsIndigo", (0.16, 0.11, 0.48), 0.50)
    yellow = material("WarmYellow", (0.95, 0.52, 0.025), 0.36)
    yellow_dark = material("Sole", (0.34, 0.20, 0.035), 0.48)
    white = material("EyeWhite", (0.98, 0.95, 0.88), 0.34)
    iris = material("IrisWarmBrown", (0.28, 0.075, 0.035), 0.28)
    pupil = material("Pupil", (0.008, 0.006, 0.009), 0.22)
    mouth_mat = material("Mouth", (0.22, 0.008, 0.016), 0.50)
    tongue = material("Tongue", (0.95, 0.18, 0.25), 0.46)
    status = material("Status", (0.10, 0.88, 0.55), 0.22)

    rounded_box("Skin_Head", (0, 0, 2.56), (1.08, 0.76, 0.88), 0.42, skin, "Head")
    ellipsoid("Skin_Ear.L", (-1.02, 0, 2.55), (0.25, 0.17, 0.31), skin, "Head")
    ellipsoid("Skin_Ear.R", (1.02, 0, 2.55), (0.25, 0.17, 0.31), skin, "Head")
    ellipsoid("Skin_EarInner.L", (-1.055, -0.155, 2.55), (0.10, 0.035, 0.16), skin_soft, "Head")
    ellipsoid("Skin_EarInner.R", (1.055, -0.155, 2.55), (0.10, 0.035, 0.16), skin_soft, "Head")
    ellipsoid("Skin_Nose", (0, -0.785, 2.45), (0.095, 0.075, 0.075), skin_soft, "Head")

    # Smooth cap, side panels and an asymmetric lock produce a designed hairline.
    ellipsoid("Hair_Cap", (0, -0.10, 3.25), (1.08, 0.78, 0.36), hair, "Head")
    rounded_box("Hair_Side.L", (-0.91, 0.02, 2.98), (0.13, 0.62, 0.27), 0.11, hair, "Head")
    rounded_box("Hair_Side.R", (0.91, 0.02, 2.98), (0.13, 0.62, 0.27), 0.11, hair, "Head")
    tube("Hair_SignatureLock", [(0.08, -0.76, 3.25), (0.28, -0.82, 3.16), (0.38, -0.79, 3.03)], 0.075, hair, "Head")

    for side, x in (("L", -0.29), ("R", 0.29)):
        eye = ellipsoid(f"White_Eye.{side}", (x, -0.765, 2.67), (0.235, 0.070, 0.255), white, "Head")
        iris_obj = ellipsoid(f"Iris_Eye.{side}", (x, -0.832, 2.66), (0.125, 0.036, 0.135), iris, "Head")
        ellipsoid(f"Pupil_Eye.{side}", (x, -0.862, 2.66), (0.068, 0.021, 0.078), pupil, "Head")
        ellipsoid(f"Glint_Eye.{side}", (x - 0.027, -0.886, 2.705), (0.020, 0.009, 0.023), white, "Head", 24, 16)
        ellipsoid(f"Lid_Eye.{side}", (x, -0.850, 2.87), (0.235, 0.025, 0.012), skin, "Head")

    tube("Brow.L", [(-0.52, -0.845, 2.99), (-0.31, -0.875, 3.05), (-0.10, -0.842, 3.00)], 0.075, hair, "Head")
    tube("Brow.R", [(0.10, -0.842, 3.00), (0.31, -0.875, 3.05), (0.52, -0.845, 2.99)], 0.075, hair, "Head")
    ellipsoid("Blush.L", (-0.61, -0.775, 2.35), (0.19, 0.028, 0.075), blush, "Head")
    ellipsoid("Blush.R", (0.61, -0.775, 2.35), (0.19, 0.028, 0.075), blush, "Head")

    mouth = ellipsoid("Mouth_Interior", (0, -0.800, 2.23), (0.34, 0.060, 0.105), mouth_mat, None)
    create_shape_keys(mouth)
    parent_to_bone(mouth, "Head")
    ellipsoid("Mouth_Teeth", (0, -0.856, 2.285), (0.22, 0.018, 0.038), white, "Head")
    ellipsoid("Mouth_Tongue", (0, -0.859, 2.185), (0.18, 0.018, 0.040), tongue, "Head")
    tube("MouthCorner.L", [(-0.34, -0.855, 2.24), (-0.25, -0.865, 2.21)], 0.024, mouth_mat, "Head")
    tube("MouthCorner.R", [(0.25, -0.865, 2.21), (0.34, -0.855, 2.24)], 0.024, mouth_mat, "Head")

    ellipsoid("Shirt_Torso", (0, 0, 1.52), (0.66, 0.43, 0.58), shirt, "Chest")
    torus("Shirt_Collar", (0, -0.02, 1.98), 0.235, 0.055, shirt_dark, "Chest")
    torus("Shirt_Hem", (0, 0, 1.06), 0.47, 0.055, shirt_dark, "Hips")
    ellipsoid("Shorts_Waist", (0, 0, 0.94), (0.63, 0.40, 0.30), shorts, "Hips")
    ellipsoid("Shorts_Leg.L", (-0.28, 0, 0.78), (0.34, 0.37, 0.27), shorts, "UpperLeg.L")
    ellipsoid("Shorts_Leg.R", (0.28, 0, 0.78), (0.34, 0.37, 0.27), shorts, "UpperLeg.R")

    # Two-piece arms and visible elbows allow readable gestures without stretching.
    for side, sign in (("L", -1), ("R", 1)):
        x = 0.66 * sign
        ellipsoid(f"Shirt_Shoulder.{side}", (x, 0, 1.67), (0.25, 0.28, 0.27), shirt, f"UpperArm.{side}")
        ellipsoid(f"Shirt_UpperArm.{side}", (0.72 * sign, 0, 1.48), (0.22, 0.24, 0.31), shirt, f"UpperArm.{side}")
        ellipsoid(f"Shirt_Elbow.{side}", (0.76 * sign, 0, 1.29), (0.225, 0.235, 0.22), shirt, f"LowerArm.{side}")
        ellipsoid(f"Shirt_Forearm.{side}", (0.77 * sign, 0, 1.15), (0.20, 0.22, 0.25), shirt, f"LowerArm.{side}")
        torus(f"Shirt_Cuff.{side}", (0.77 * sign, 0, 0.99), 0.19, 0.045, shirt_dark, f"Hand.{side}")
        ellipsoid(f"Skin_Hand.{side}", (0.77 * sign, -0.01, 0.90), (0.22, 0.18, 0.22), skin, f"Hand.{side}")
        for index, x_offset in enumerate((-0.09, 0.0, 0.09)):
            ellipsoid(
                f"Skin_Finger{index + 1}.{side}",
                (0.77 * sign + x_offset, -0.035, 0.79),
                (0.062, 0.075, 0.14),
                skin,
                f"Hand.{side}",
                28,
                18,
            )

    for side, x in (("L", -0.23), ("R", 0.23)):
        ellipsoid(f"Skin_Leg.{side}", (x, 0, 0.46), (0.20, 0.20, 0.27), skin, f"LowerLeg.{side}")
        torus(f"Sock_Rib.{side}", (x, 0, 0.25), 0.18, 0.045, white, f"LowerLeg.{side}")
        rounded_box(f"Shoe_Body.{side}", (x, -0.14, 0.15), (0.30, 0.42, 0.14), 0.13, yellow, f"Foot.{side}")
        rounded_box(f"Shoe_Sole.{side}", (x, -0.15, 0.055), (0.32, 0.44, 0.045), 0.035, yellow_dark, f"Foot.{side}")
        for lace_x in (-0.075, 0.075):
            tube(
                f"Shoe_Lace{lace_x}.{side}",
                [(x + lace_x, -0.535, 0.18), (x - lace_x, -0.535, 0.22)],
                0.012,
                white,
                f"Foot.{side}",
            )

    # Four-petal assistant status pin remains the app-specific identity marker.
    pin_center = Vector((0.34, -0.445, 1.55))
    for index, offset in enumerate(((0, 0, 0.08), (-0.08, 0, 0), (0.08, 0, 0), (0, 0, -0.08))):
        ellipsoid(f"Status_Petal{index + 1}", pin_center + Vector(offset), (0.065, 0.025, 0.085), white, "Chest", 28, 18)
    ellipsoid("Status_Orb", pin_center + Vector((0, -0.035, 0)), (0.067, 0.028, 0.067), status, "Chest", 28, 18)


def setup_preview():
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    floor = bpy.context.object
    floor.name = "PreviewFloor"
    floor.data.materials.append(material("PreviewFloorMat", (0.055, 0.075, 0.085), 0.62))

    bpy.ops.object.camera_add(location=(0, -8.2, 3.0))
    camera = bpy.context.object
    camera.name = "PreviewCamera"
    camera.data.lens = 64
    camera.data.sensor_width = 36
    direction = Vector((0, 0, 1.75)) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera

    def light(name, kind, location, energy, color, size=3.0):
        data = bpy.data.lights.new(name, kind)
        data.energy = energy
        data.color = color
        if kind == "AREA":
            data.shape = "DISK"
            data.size = size
        obj = bpy.data.objects.new(name, data)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (math.radians(58), 0, math.radians(25))
        return obj

    key = light("KeyLight", "AREA", (-3.4, -4.2, 6.2), 1050, (1.0, 0.78, 0.67), 4.0)
    key.rotation_euler = (Vector((0, 0, 2.0)) - key.location).to_track_quat("-Z", "Y").to_euler()
    fill = light("FillLight", "AREA", (3.8, -2.0, 3.8), 800, (0.40, 0.78, 1.0), 3.0)
    fill.rotation_euler = (Vector((0, 0, 1.9)) - fill.location).to_track_quat("-Z", "Y").to_euler()
    rim = light("RimLight", "AREA", (0, 2.7, 4.5), 1000, (0.35, 1.0, 0.75), 2.5)
    rim.rotation_euler = (Vector((0, 0, 2.1)) - rim.location).to_track_quat("-Z", "Y").to_euler()

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 1200
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(PREVIEW_PATH)
    scene.render.film_transparent = False
    scene.world.color = (0.018, 0.028, 0.035)
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.render.image_settings.color_mode = "RGBA"


def save_and_export():
    ART_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_PATH))
    bpy.ops.render.render(write_still=True)

    bpy.ops.object.select_all(action="DESELECT")
    for obj in CHARACTER_OBJECTS:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = RIG
    bpy.ops.export_scene.fbx(
        filepath=str(FBX_PATH),
        use_selection=True,
        object_types={"ARMATURE", "MESH"},
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_UNITS",
        axis_forward="-Z",
        axis_up="Y",
        add_leaf_bones=False,
        bake_anim=False,
        mesh_smooth_type="FACE",
        use_mesh_modifiers=True,
    )
    print(f"RIG_BUILD_COMPLETE blend={BLEND_PATH} fbx={FBX_PATH} preview={PREVIEW_PATH}")


CHARACTER_OBJECTS = []
reset_scene()
RIG = create_rig()
build_character()
setup_preview()
save_and_export()
