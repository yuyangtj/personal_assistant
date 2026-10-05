"""Converts "Cool Man" (ardhanaputra, CC-BY 4.0) into the assistant's realistic Unity character.

Run from the Android assistant repository root:

    blender --background --python tools/blender/build_cool_man_character.py

Source: design/source/cool_man.glb (https://sketchfab.com/3d-models/cool-man-ad14b71697dd4ea7836c1f06c75e5f72).
Modifications: rig clean-up and renaming, merged meshes, jaw/eye bones, a status light,
and procedurally generated facial blendshapes for English lip-sync, blinking and brows.
"""

from pathlib import Path
import json
import math
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "design" / "source" / "cool_man.glb"
UNITY_ASSETS = ROOT / "unity" / "AvatarPrototype" / "Assets" / "AvatarPrototype"
FBX_PATH = UNITY_ASSETS / "Resources" / "Character" / "CoolManRig.fbx"
CHARACTER_DIR = UNITY_ASSETS / "Characters" / "CoolMan"
TEXTURE_DIR = CHARACTER_DIR / "Textures"
MANIFEST_PATH = CHARACTER_DIR / "CoolManMaterials.json"
BLEND_PATH = ROOT / "design" / "source" / "CoolManRig.blend"
RENDER_DIR = ROOT / "design" / "renders" / "cool-man"

KEEP_ACTIONS = ("salute", "shakehand")

BONE_NAMES = {
    "Hips": "Hips", "Spine": "Spine", "Spine1": "Spine1", "Spine2": "Chest", "Neck": "Neck", "Head": "Head",
    "LeftShoulder": "Shoulder.L", "LeftArm": "UpperArm.L", "LeftForeArm": "LowerArm.L", "LeftHand": "Hand.L",
    "RightShoulder": "Shoulder.R", "RightArm": "UpperArm.R", "RightForeArm": "LowerArm.R", "RightHand": "Hand.R",
    "LeftUpLeg": "UpperLeg.L", "LeftLeg": "LowerLeg.L", "LeftFoot": "Foot.L", "LeftToeBase": "Toe.L",
    "RightUpLeg": "UpperLeg.R", "RightLeg": "LowerLeg.R", "RightFoot": "Foot.R", "RightToeBase": "Toe.R",
}
FINGERS = ("Thumb", "Index", "Middle", "Ring", "Pinky")

MATERIAL_NAMES = {
    "Wolf3D_Skin": "CoolMan_Skin", "Wolf3D_Body": "CoolMan_Body", "Wolf3D_Teeth": "CoolMan_Teeth",
    "Wolf3D_Eye": "CoolMan_Eye", "Wolf3D_Hair": "CoolMan_Hair", "Wolf3D_Glasses": "CoolMan_Glasses",
    "Wolf3D_Outfit_Top": "CoolMan_Top", "Wolf3D_Outfit_Bottom": "CoolMan_Bottom",
    "Wolf3D_Outfit_Footwear": "CoolMan_Footwear",
}
FACE_MATERIALS = {"CoolMan_Skin", "CoolMan_Teeth", "CoolMan_Eye"}


def log(message):
    print(f"COOLMAN: {message}")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Import and clean-up


def import_source():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(SOURCE))
    armature = next(obj for obj in bpy.data.objects if obj.type == "ARMATURE")
    for obj in list(bpy.data.objects):
        if obj.type == "MESH" and obj.parent is None:
            bpy.data.objects.remove(obj)  # Stray Icosphere.

    world = armature.matrix_world.copy()
    armature.parent = None
    armature.matrix_world = world
    for obj in list(bpy.data.objects):
        if obj.type == "EMPTY":
            bpy.data.objects.remove(obj)
    armature.name = "CoolManRig"
    armature.data.name = "CoolManRigArmature"
    armature.animation_data.action = None
    armature.data.pose_position = "REST"
    return armature


def action_fcurves(action):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                yield bag, list(bag.fcurves)


def deforming_bones():
    names = set()
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        groups = [group.name for group in obj.vertex_groups]
        for vertex in obj.data.vertices:
            for element in vertex.groups:
                if element.weight > 0.0:
                    names.add(groups[element.group])
    return names


def clean_name(name):
    base = name.split(":", 1)[-1].rsplit("_", 1)[0]
    if base in BONE_NAMES:
        return BONE_NAMES[base]
    for finger in FINGERS:
        for side, suffix in (("Left", "L"), ("Right", "R")):
            prefix = f"{side}Hand{finger}"
            if base.startswith(prefix):
                return f"{finger}{base[len(prefix):]}.{suffix}"
    return None


def clean_rig(armature):
    keep = deforming_bones()
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    for bone in list(armature.data.edit_bones):
        if bone.name not in keep:
            armature.data.edit_bones.remove(bone)
    bpy.ops.object.mode_set(mode="OBJECT")

    renames = {}
    for bone in armature.data.bones:
        new = clean_name(bone.name)
        if new is None:
            raise RuntimeError(f"No clean name for bone {bone.name}")
        renames[bone.name] = new
    for old, new in renames.items():
        armature.data.bones[old].name = new
        for obj in bpy.data.objects:
            if obj.type == "MESH" and old in obj.vertex_groups:
                obj.vertex_groups[old].name = new

    bone_names = {bone.name for bone in armature.data.bones}
    for action in list(bpy.data.actions):
        if action.name not in KEEP_ACTIONS:
            bpy.data.actions.remove(action)
            continue
        for bag, fcurves in action_fcurves(action):
            for fcurve in fcurves:
                path = fcurve.data_path
                if 'pose.bones["' not in path:
                    continue
                bone = path.split('"')[1]
                bone = renames.get(bone, bone)
                if bone not in bone_names:
                    bag.fcurves.remove(fcurve)
                    continue
                fcurve.data_path = f'pose.bones["{bone}"]' + path.split('"]', 1)[1]
                # Hips keeps its height bob but not horizontal travel toward the camera.
                if bone == "Hips" and path.endswith("location") and fcurve.array_index in (0, 2):
                    first = fcurve.keyframe_points[0].co[1]
                    for point in fcurve.keyframe_points:
                        point.co[1] = first
                        point.handle_left[1] = first
                        point.handle_right[1] = first
    log(f"rig cleaned: bones={len(armature.data.bones)} actions={[a.name for a in bpy.data.actions]}")


# ---------------------------------------------------------------------------
# Meshes, materials, textures


def clean_material_name(material):
    base = material.name.rsplit(".", 1)[0]
    return MATERIAL_NAMES.get(base, base)


def export_textures():
    TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for material in bpy.data.materials:
        if not material.use_nodes:
            continue
        name = clean_material_name(material)
        material.name = name
        entry = {"name": name, "alphaClip": name == "CoolMan_Hair", "smoothness": 0.35}
        for node in material.node_tree.nodes:
            if node.type != "TEX_IMAGE" or node.image is None:
                continue
            targets = {(link.to_node.type, link.to_socket.name) for output in node.outputs for link in output.links}
            if any(kind == "NORMAL_MAP" for kind, _ in targets):
                role = "normal"
            elif any(socket == "Base Color" for _, socket in targets):
                role = "baseColor"
            else:
                role = "mask"
            filename = f"{name}_{role}.png"
            image = node.image
            image.filepath_raw = str(TEXTURE_DIR / filename)
            image.file_format = "PNG"
            image.save()
            entry[role] = filename
        manifest[name] = entry
    manifest["CoolMan_Glasses"]["smoothness"] = 0.8
    manifest["CoolMan_Eye"]["smoothness"] = 0.75
    manifest["CoolMan_Teeth"]["smoothness"] = 0.3
    manifest["CoolMan_Skin"]["smoothness"] = 0.22
    manifest["CoolMan_Hair"]["smoothness"] = 0.25
    # Untextured materials created later in this script.
    manifest["CoolMan_StatusLight"] = {"name": "CoolMan_StatusLight", "color": [0.1, 0.88, 0.55, 1.0], "emission": True, "smoothness": 0.6}
    # Unity's JsonUtility reads arrays of objects, not dictionaries.
    MANIFEST_PATH.write_text(json.dumps({"materials": list(manifest.values())}, indent=2) + "\n")
    log(f"textures exported: {sorted(manifest)}")


def join(objects, name):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    merged = bpy.context.view_layer.objects.active
    merged.name = name
    merged.data.name = name
    return merged


def merge_meshes(armature):
    face_parts, body_parts, eyes = [], [], []
    for obj in [obj for obj in bpy.data.objects if obj.type == "MESH"]:
        material = obj.data.materials[0].name
        if material == "CoolMan_Eye":
            eyes.append(obj)
        (face_parts if material in FACE_MATERIALS else body_parts).append(obj)

    # Eye discs follow their own bones so gaze can slide them inside the eyelids.
    for eye in eyes:
        world = [eye.matrix_world @ v.co for v in eye.data.vertices]
        side = "L" if sum(p.x for p in world) > 0 else "R"
        for group in list(eye.vertex_groups):
            eye.vertex_groups.remove(group)
        group = eye.vertex_groups.new(name=f"Eye.{side}")
        group.add(range(len(eye.data.vertices)), 1.0, "REPLACE")

    skin = next(obj for obj in face_parts if obj.data.materials[0].name == "CoolMan_Skin")
    face_parts.remove(skin)
    face = join([skin] + face_parts, "Assistant_Face")
    body = join(body_parts, "Assistant_Body")
    for obj in (face, body):
        obj.parent = armature
        if not any(mod.type == "ARMATURE" for mod in obj.modifiers):
            modifier = obj.modifiers.new("Armature", "ARMATURE")
            modifier.object = armature
    return face, body


def add_head_bones(armature, face):
    world = face.matrix_world
    eye_centers = {}
    material_index = face.data.materials.find("CoolMan_Eye")
    for side, sign in (("L", 1), ("R", -1)):
        points = [world @ face.data.vertices[v].co for poly in face.data.polygons if poly.material_index == material_index
                  for v in poly.vertices if (world @ face.data.vertices[v].co).x * sign > 0]
        eye_centers[side] = sum(points, Vector()) / len(points)

    inverse = armature.matrix_world.inverted()
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    bones = armature.data.edit_bones
    head = bones["Head"]
    jaw = bones.new("Jaw")
    jaw.head = inverse @ Vector((0.0, -0.005, 1.635))
    jaw.tail = inverse @ Vector((0.0, -0.08, 1.56))
    jaw.parent = head
    for side, center in eye_centers.items():
        eye = bones.new(f"Eye.{side}")
        eye.head = inverse @ center
        eye.tail = inverse @ (center + Vector((0.0, -0.03, 0.0)))
        eye.parent = head
    bpy.ops.object.mode_set(mode="OBJECT")
    jaw_bone = armature.data.bones["Jaw"]
    jaw_bone.use_deform = False
    log(f"head bones added: eyes={ {k: [round(c, 3) for c in v] for k, v in eye_centers.items()} }")
    return eye_centers


# ---------------------------------------------------------------------------
# Facial blendshapes: smooth deformation fields driven by measured landmarks


def smoothstep(edge0, edge1, value):
    t = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def interpolate(curve, x):
    """Linear interpolation over [(x, value)] sorted by x, clamped at both ends."""
    if x <= curve[0][0]:
        return curve[0][1]
    for (x0, v0), (x1, v1) in zip(curve, curve[1:]):
        if x <= x1:
            t = (x - x0) / max(x1 - x0, 1e-9)
            return v0 + (v1 - v0) * t
    return curve[-1][1]


def binned_curve(points, bins=14):
    """Averages (x, value) samples into bins so interpolation is stable on dense loops."""
    xs = [p[0] for p in points]
    lo, hi = min(xs), max(xs)
    buckets = {}
    for x, value in points:
        index = min(bins - 1, int((x - lo) / max(hi - lo, 1e-9) * bins))
        buckets.setdefault(index, []).append((x, value))
    return [(sum(p[0] for p in b) / len(b), sum(p[1] for p in b) / len(b)) for _, b in sorted(buckets.items())]


class FaceRig:
    """Landmarks of the merged face mesh in world space (metres, +x = character left, -y = front)."""

    def __init__(self, face):
        self.face = face
        self.world = face.matrix_world.copy()
        self.inverse = self.world.inverted()
        mesh = face.data
        self.base = [self.world @ v.co for v in mesh.vertices]
        skin = mesh.materials.find("CoolMan_Skin")
        teeth = mesh.materials.find("CoolMan_Teeth")
        eye = mesh.materials.find("CoolMan_Eye")
        self.material = [None] * len(mesh.vertices)
        for poly in mesh.polygons:
            for index in poly.vertices:
                self.material[index] = poly.material_index
        self.skin, self.teeth, self.eye = skin, teeth, eye

        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        boundary = {}
        for edge in bm.edges:
            if edge.is_boundary and all(self.material[v.index] == skin for v in edge.verts):
                for vert in edge.verts:
                    faces_z = [ (self.world @ f.calc_center_median()).z for f in vert.link_faces]
                    boundary[vert.index] = sum(faces_z) / len(faces_z)
        bm.free()

        # Lip line: the split between the lips at the front of the mouth.
        # The inner seam where the lips meet (UV-seam boundaries on the lip front are excluded).
        lips = [i for i in boundary if abs(self.base[i].z - 1.601) < 0.006 and -0.114 < self.base[i].y < -0.09 and abs(self.base[i].x) < 0.035]
        self.upper_lip = {i for i in lips if boundary[i] > self.base[i].z}
        self.lower_lip = set(lips) - self.upper_lip
        self.lip_curve = binned_curve([(self.base[i].x, self.base[i].z) for i in lips])
        self.corner_x = max(abs(self.base[i].x) for i in lips)
        self.mouth_center = Vector((0.0, min(self.base[i].y for i in lips), interpolate(self.lip_curve, 0.0)))

        # Eyelid rims per side, split into upper and lower curves.
        self.eyes = {}
        for side, sign in (("L", 1), ("R", -1)):
            rim = [i for i in boundary if 0.012 < self.base[i].x * sign < 0.056 and abs(self.base[i].z - 1.669) < 0.013 and self.base[i].y < -0.08]
            center_z = sum(self.base[i].z for i in rim) / len(rim)
            upper = [(self.base[i].x, self.base[i].z) for i in rim if self.base[i].z >= center_z]
            lower = [(self.base[i].x, self.base[i].z) for i in rim if self.base[i].z < center_z]
            xs = [self.base[i].x for i in rim]
            self.eyes[side] = dict(sign=sign, upper=binned_curve(upper, 10), lower=binned_curve(lower, 10),
                                   x_min=min(xs), x_max=max(xs), center_z=center_z, rim=set(rim))

        # Teeth are separate pieces; whole pieces below the lip line move with the jaw.
        neighbours = {}
        for edge in mesh.edges:
            a, b = edge.vertices
            if self.material[a] == teeth:
                neighbours.setdefault(a, []).append(b)
                neighbours.setdefault(b, []).append(a)
        self.lower_teeth, seen = set(), set()
        for start in neighbours:
            if start in seen:
                continue
            piece, stack = [], [start]
            seen.add(start)
            while stack:
                vertex = stack.pop()
                piece.append(vertex)
                for other in neighbours[vertex]:
                    if other not in seen:
                        seen.add(other)
                        stack.append(other)
            if sum(self.base[i].z for i in piece) / len(piece) < self.mouth_center.z - 0.0005:
                self.lower_teeth.update(piece)

        # Vertices duplicated at UV seams must move together, except across the lip seam.
        groups = {}
        for index, position in enumerate(self.base):
            groups.setdefault((round(position.x, 5), round(position.y, 5), round(position.z, 5)), []).append(index)
        self.seam_groups = [g for g in groups.values() if len(g) > 1 and not (set(g) & self.upper_lip and set(g) & self.lower_lip)]
        self.lower_weight = self.jaw_regions(mesh)
        log(f"landmarks: lip verts={len(lips)} corners=±{self.corner_x:.4f} mouth={[round(c, 4) for c in self.mouth_center]} "
            f"eye rims={ {k: len(v['rim']) for k, v in self.eyes.items()} } lowerTeeth={len(self.lower_teeth)}")

    def jaw_regions(self, mesh):
        """Lower-jaw membership from surface distance to a chin seed versus an upper-lip seed.

        The lip seam is open, so paths must travel around the mouth corners: lips separate
        cleanly while the corners and the mouth interior get a smooth blend.
        """
        import heapq

        graph = {i: [] for i, m in enumerate(self.material) if m == self.skin}
        for edge in mesh.edges:
            a, b = edge.vertices
            if a in graph and b in graph:
                length = (self.base[a] - self.base[b]).length
                graph[a].append((b, length))
                graph[b].append((a, length))
        for group in self.seam_groups:
            members = [i for i in group if i in graph]
            for a in members:
                graph[a].extend((b, 0.0) for b in members if b != a)

        def nearest(target):
            return min(graph, key=lambda i: (self.base[i] - target).length)

        def distances(seed):
            result = {seed: 0.0}
            queue = [(0.0, seed)]
            while queue:
                distance, vertex = heapq.heappop(queue)
                if distance > result.get(vertex, 1e9):
                    continue
                for other, length in graph[vertex]:
                    candidate = distance + length
                    if candidate < result.get(other, 1e9):
                        result[other] = candidate
                        heapq.heappush(queue, (candidate, other))
            return result

        center = self.mouth_center
        to_lower = distances(nearest(Vector((0.0, center.y - 0.008, center.z - 0.022))))
        to_upper = distances(nearest(Vector((0.0, center.y - 0.012, center.z + 0.018))))
        weights = [0.0] * len(self.base)
        for i in graph:
            du, dl = to_upper.get(i, 1.0), to_lower.get(i, 1.0)
            weights[i] = smoothstep(0.40, 0.60, du / max(du + dl, 1e-9))

        return weights

    # --- regions ----------------------------------------------------------
    def line_offset(self, p):
        return p.z - interpolate(self.lip_curve, max(-self.corner_x, min(self.corner_x, p.x)))

    def is_lower(self, i, p):
        if self.material[i] == self.teeth:
            return i in self.lower_teeth
        return self.lower_weight[i] > 0.5

    def mouth_weight(self, p, rx=0.05, rz=0.04):
        if p.y > -0.02:
            return 0.0
        dz = self.line_offset(p)
        e = math.sqrt((p.x / rx) ** 2 + (dz / rz) ** 2)
        return smoothstep(1.0, 0.3, e)

    def lip_band(self, p, width=0.008):
        dz = self.line_offset(p)
        across = smoothstep(self.corner_x + 0.012, self.corner_x * 0.6, abs(p.x))
        return math.exp(-(dz / width) ** 2) * across

    # --- fields (return world-space displacement for vertex i) -------------
    def jaw(self, i, p, degrees):
        if self.material[i] == self.eye or (self.material[i] == self.teeth and i not in self.lower_teeth):
            return Vector()
        lateral = 1.0 - smoothstep(0.035, 0.085, abs(p.x))
        # Keep the seam anchored at its endpoints. Use a full-width seam band here rather
        # than lip_band(): lip_band intentionally fades laterally and left the last corner
        # triangles partially attached to the rotating jaw.
        near_seam = math.exp(-(self.line_offset(p) / 0.012) ** 2)
        corner_anchor = smoothstep(self.corner_x, self.corner_x * 0.68, abs(p.x))
        lateral *= (1.0 - near_seam) + corner_anchor * near_seam
        throat = smoothstep(-0.02, -0.075, p.y) if p.z < 1.57 else 1.0  # Keep the neck still.
        weight = lateral * self.lower_weight[i] * throat
        if i in self.lower_teeth:
            weight = 1.0
        if weight <= 0.0:
            return Vector()
        pivot = Vector((0.0, -0.005, 1.635))
        rotation = Matrix.Rotation(math.radians(degrees * weight), 3, "X")
        return (rotation @ (p - pivot) + pivot) - p

    def apart(self, i, p, amount):
        if self.material[i] != self.skin:
            return Vector()
        corner_anchor = smoothstep(self.corner_x, self.corner_x * 0.68, abs(p.x))
        band = self.lip_band(p, 0.010) * corner_anchor
        direction = 1.0 - 2.0 * self.lower_weight[i]
        return Vector((0.0, 0.0, amount * band * direction))

    def pucker(self, i, p, amount):
        if self.material[i] == self.eye:
            return Vector()
        weight = self.mouth_weight(p, 0.055, 0.035) * (0.4 + 0.6 * self.lip_band(p, 0.014))
        if i in self.lower_teeth or self.material[i] == self.teeth:
            weight *= 0.2
        dx = -p.x * 0.38 * weight * amount
        dy = -0.0065 * weight * amount
        dz = -self.line_offset(p) * 0.25 * weight * amount
        return Vector((dx, dy, dz))

    def wide(self, i, p, amount):
        if self.material[i] != self.skin:
            return Vector()
        weight = self.mouth_weight(p, 0.06, 0.03) * smoothstep(0.0, self.corner_x, abs(p.x))
        return Vector((math.copysign(0.0045, p.x) * weight * amount, 0.002 * weight * amount, 0.0008 * weight * amount))

    def press(self, i, p, amount):
        if self.material[i] != self.skin:
            return Vector()
        band = self.lip_band(p, 0.007)
        return Vector((0.0, 0.0018 * band * amount, -self.line_offset(p) * 0.35 * band * amount))

    def tuck(self, i, p, amount):
        if self.material[i] != self.skin or not self.is_lower(i, p):
            return Vector()
        band = self.lip_band(p, 0.009)
        return Vector((0.0, 0.0042 * band * amount, 0.0038 * band * amount))

    def corner(self, i, p, side_sign, amount):
        if self.material[i] != self.skin or p.x * side_sign <= -0.005:
            return Vector()
        cx = self.corner_x * side_sign
        weight = math.exp(-(((p.x - cx) / 0.016) ** 2 + (self.line_offset(p) / 0.016) ** 2))
        cheek = math.exp(-(((p.x - cx * 1.35) / 0.02) ** 2 + ((p.z - 1.625) / 0.02) ** 2)) * 0.45
        w = weight + cheek
        return Vector((side_sign * 0.0025 * w * max(amount, 0.0), 0.0018 * w, 0.0038 * w)) * abs(amount) * (1 if amount > 0 else 1) \
            if amount > 0 else Vector((0.0, 0.0, -0.0032 * weight)) * abs(amount)

    def blink(self, i, p, side):
        eye = self.eyes[side]
        if self.material[i] != self.skin or p.y > -0.06:
            return Vector()
        if not (eye["x_min"] - 0.006 < p.x < eye["x_max"] + 0.006):
            return Vector()
        across = smoothstep(eye["x_min"] - 0.006, eye["x_min"] + 0.004, p.x) * smoothstep(eye["x_max"] + 0.006, eye["x_max"] - 0.004, p.x)
        upper = interpolate(eye["upper"], p.x)
        lower = interpolate(eye["lower"], p.x)
        gap = upper - lower
        if i in eye["rim"]:
            if p.z < eye["center_z"]:
                return Vector((0.0, -0.0006, gap * 0.10 * across))
            # Upper rim vertices always travel the full distance to meet the lower lid.
            return Vector((0.0, -0.0022 * across, -(p.z - lower) * 0.92 * across))
        above = p.z - upper
        if above < -0.0015 or above > 0.013:
            return Vector()
        weight = smoothstep(0.013, 0.0, max(above, 0.0)) * across
        return Vector((0.0, -0.0022 * weight, -(gap * 0.92) * weight))

    def brow(self, i, p, side_sign, raise_outer=0.0, raise_inner=0.0, down=0.0):
        if self.material[i] != self.skin or p.y > -0.05:
            return Vector()
        if side_sign and p.x * side_sign < -0.004:
            return Vector()
        vertical = smoothstep(1.676, 1.692, p.z) * smoothstep(1.748, 1.712, p.z)
        lateral = smoothstep(0.075, 0.055, abs(p.x))
        weight = vertical * lateral
        outer = smoothstep(0.018, 0.055, abs(p.x))
        inner = 1.0 - smoothstep(0.008, 0.04, abs(p.x))
        dz = (0.0045 * raise_outer * outer + 0.0045 * raise_inner * inner - 0.0032 * down) * weight
        dx = -math.copysign(0.0014, p.x) * down * weight * inner
        return Vector((dx, 0.0, dz))

    def cheek_puff(self, i, p):
        if self.material[i] != self.skin:
            return Vector()
        side = math.copysign(1.0, p.x)
        center = Vector((0.046 * side, -0.082, 1.612))
        weight = math.exp(-((p - center).length / 0.024) ** 2)
        return Vector((0.0028 * side, -0.0022, 0.0)) * weight

    # --- shapes ------------------------------------------------------------
    def shape(self, recipe):
        positions = []
        for i, p in enumerate(self.base):
            offset = Vector()
            for field, *args in recipe:
                offset += getattr(self, field)(i, p, *args)
            positions.append(p + offset)
        for group in self.seam_groups:
            average = sum((positions[i] for i in group), Vector()) / len(group)
            for i in group:
                positions[i] = average.copy()
        return positions


VISEMES = {
    "viseme_sil": [],
    "viseme_PP": [("press", 1.0)],
    "viseme_FF": [("jaw", 1.5), ("tuck", 1.0), ("apart", 0.0012)],
    "viseme_TH": [("jaw", 2.5), ("apart", 0.0022)],
    "viseme_DD": [("jaw", 3.0), ("apart", 0.0015), ("wide", 0.2)],
    "viseme_kk": [("jaw", 3.8), ("apart", 0.001)],
    "viseme_CH": [("jaw", 2.0), ("pucker", 0.6), ("apart", 0.0028)],
    "viseme_SS": [("jaw", 1.0), ("wide", 0.55), ("apart", 0.0018)],
    "viseme_nn": [("jaw", 2.3), ("apart", 0.001)],
    "viseme_RR": [("jaw", 2.0), ("pucker", 0.5), ("apart", 0.0015)],
    "viseme_aa": [("jaw", 6.5), ("apart", 0.0025)],
    "viseme_E": [("jaw", 3.5), ("wide", 0.5), ("apart", 0.002)],
    "viseme_ih": [("jaw", 2.5), ("wide", 0.7), ("apart", 0.0015)],
    "viseme_oh": [("jaw", 4.5), ("pucker", 0.7), ("apart", 0.002)],
    "viseme_ou": [("jaw", 2.0), ("pucker", 1.0), ("apart", 0.0015)],
}
SUPPORT = {
    "jawOpen": [("jaw", 9.0)],
    "mouthSmile.L": [("corner", 1, 1.0)],
    "mouthSmile.R": [("corner", -1, 1.0)],
    "mouthFrown.L": [("corner", 1, -1.0)],
    "mouthFrown.R": [("corner", -1, -1.0)],
    "eyeBlink.L": [("blink", "L")],
    "eyeBlink.R": [("blink", "R")],
    "browInnerUp": [("brow", 0, 0.0, 1.0, 0.0)],
    "browOuterUp.L": [("brow", 1, 1.0, 0.0, 0.0)],
    "browOuterUp.R": [("brow", -1, 1.0, 0.0, 0.0)],
    "browDown.L": [("brow", 1, 0.0, 0.0, 1.0)],
    "browDown.R": [("brow", -1, 0.0, 0.0, 1.0)],
    "cheekPuff": [("cheek_puff",)],
}


def build_shape_keys(face):
    rig = FaceRig(face)
    face.shape_key_add(name="Basis")
    report = {}
    for name, recipe in {**VISEMES, **SUPPORT}.items():
        positions = rig.shape(recipe)
        key = face.shape_key_add(name=name)
        key.value = 0.0
        largest = 0.0
        for index, position in enumerate(positions):
            key.data[index].co = rig.inverse @ position
            largest = max(largest, (position - rig.base[index]).length)
        report[name] = (positions, largest)

    def lip_gap(positions):
        upper = [positions[i] for i in rig.upper_lip if abs(rig.base[i].x) < 0.006]
        lower = [positions[i] for i in rig.lower_lip if abs(rig.base[i].x) < 0.006]
        return (sum(p.z for p in upper) / len(upper)) - (sum(p.z for p in lower) / len(lower))

    def corner_gap(positions):
        threshold = rig.corner_x * 0.85
        upper = [positions[i] for i in rig.upper_lip if abs(rig.base[i].x) > threshold]
        lower = [positions[i] for i in rig.lower_lip if abs(rig.base[i].x) > threshold]
        return (sum(p.z for p in upper) / len(upper)) - (sum(p.z for p in lower) / len(lower))

    def lid_gap(positions, side):
        eye = rig.eyes[side]
        upper = [positions[i].z for i in eye["rim"] if rig.base[i].z >= eye["center_z"] and abs(rig.base[i].x - eye["sign"] * 0.033) < 0.006]
        lower = [positions[i].z for i in eye["rim"] if rig.base[i].z < eye["center_z"] and abs(rig.base[i].x - eye["sign"] * 0.033) < 0.006]
        return sum(upper) / len(upper) - sum(lower) / len(lower)

    base_gap = lip_gap(rig.base)
    base_corner_gap = corner_gap(rig.base)
    checks = {
        "PP gap change (mm)": ((lip_gap(report["viseme_PP"][0]) - base_gap) * 1000, lambda v: v <= 0.3),
        "aa opening (mm)": ((lip_gap(report["viseme_aa"][0]) - base_gap) * 1000, lambda v: 9.0 < v < 18.0),
        "oh opening (mm)": ((lip_gap(report["viseme_oh"][0]) - base_gap) * 1000, lambda v: 5.0 < v < 14.0),
        "ou opening (mm)": ((lip_gap(report["viseme_ou"][0]) - base_gap) * 1000, lambda v: 1.5 < v < 9.0),
        "aa corner opening (mm)": ((corner_gap(report["viseme_aa"][0]) - base_corner_gap) * 1000, lambda v: v < 2.0),
        "jaw corner opening (mm)": ((corner_gap(report["jawOpen"][0]) - base_corner_gap) * 1000, lambda v: v < 2.0),
        "blink L lid gap (mm)": (lid_gap(report["eyeBlink.L"][0], "L") * 1000, lambda v: v < 1.5),
        "blink R lid gap (mm)": (lid_gap(report["eyeBlink.R"][0], "R") * 1000, lambda v: v < 1.5),
        "open L lid gap (mm)": (lid_gap(rig.base, "L") * 1000, lambda v: v > 8.0),
        "max displacement (mm)": (max(v[1] for v in report.values()) * 1000, lambda v: v < 25.0),
    }
    failed = []
    for label, (value, ok) in checks.items():
        status = "ok" if ok(value) else "FAIL"
        if status == "FAIL":
            failed.append(label)
        log(f"check {label}: {value:.2f} {status}")
    return rig, failed


# ---------------------------------------------------------------------------
# Status light, review renders, export


def add_status_orb(armature, body):
    from mathutils.bvhtree import BVHTree

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = body.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    top = mesh.materials.find("CoolMan_Top")
    verts = [body.matrix_world @ v.co for v in mesh.vertices]
    polys = [tuple(p.vertices) for p in mesh.polygons if p.material_index == top]
    tree = BVHTree.FromPolygons(verts, polys)
    hit, normal, _, _ = tree.ray_cast(Vector((0.085, -1.0, 1.405)), Vector((0.0, 1.0, 0.0)))
    evaluated.to_mesh_clear()
    if hit is None:
        raise RuntimeError("Status light could not find the coat surface")

    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=10, radius=0.0075, location=(0, 0, 0))
    orb = bpy.context.object
    orb.name = "Status_Orb"
    orb.data.name = "Status_Orb"
    orb.scale = (1.0, 0.55, 1.0)
    bpy.ops.object.transform_apply(scale=True)
    material = bpy.data.materials.new("CoolMan_StatusLight")
    material.use_nodes = True
    shader = material.node_tree.nodes["Principled BSDF"]
    shader.inputs["Base Color"].default_value = (0.1, 0.88, 0.55, 1.0)
    shader.inputs["Emission Color"].default_value = (0.1, 0.88, 0.55, 1.0)
    shader.inputs["Emission Strength"].default_value = 2.0
    orb.data.materials.append(material)
    for poly in orb.data.polygons:
        poly.use_smooth = True

    orb.location = hit + normal * 0.004
    bpy.context.view_layer.update()
    world = orb.matrix_world.copy()
    orb.parent = armature
    orb.parent_type = "BONE"
    orb.parent_bone = "Chest"
    bpy.context.view_layer.update()
    orb.matrix_world = world
    log(f"status light at {[round(c, 3) for c in hit]}")
    return orb


def render_reviews(armature, face):
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 700
    scene.render.resolution_y = 900
    scene.view_settings.look = "AgX - Base Contrast"
    world = bpy.data.worlds.new("Studio")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.86, 0.82, 0.78, 1.0)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.8
    scene.world = world

    def light(name, location, energy, size):
        data = bpy.data.lights.new(name, "AREA")
        data.energy = energy
        data.size = size
        obj = bpy.data.objects.new(name, data)
        scene.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (Vector((0, 0, 1.55)) - Vector(location)).to_track_quat("-Z", "Y").to_euler()

    light("Key", (-1.2, -1.6, 2.2), 180, 1.5)
    light("Fill", (1.4, -1.2, 1.7), 70, 1.5)
    light("Rim", (0.4, 1.4, 2.0), 90, 1.0)

    camera = bpy.data.objects.new("ReviewCamera", bpy.data.cameras.new("ReviewCamera"))
    scene.collection.objects.link(camera)
    scene.camera = camera

    def shot(path, location, target, lens):
        camera.location = location
        camera.data.lens = lens
        camera.rotation_euler = (Vector(target) - Vector(location)).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)

    def set_shapes(**values):
        for key in face.data.shape_keys.key_blocks[1:]:
            key.value = values.get(key.name, 0.0)

    armature.data.pose_position = "REST"
    set_shapes()
    shot(RENDER_DIR / "chest.png", (0.0, -1.55, 1.52), (0.0, 0.0, 1.46), 50)
    for name in ["viseme_sil"] + [n for n in VISEMES if n != "viseme_sil"] + ["jawOpen"]:
        set_shapes(**{name: 1.0})
        shot(RENDER_DIR / f"face-{name}.png", (0.0, -0.55, 1.63), (0.0, 0.0, 1.625), 85)
    for name, values in {
        "blink": {"eyeBlink.L": 1.0, "eyeBlink.R": 1.0},
        "smile": {"mouthSmile.L": 1.0, "mouthSmile.R": 1.0},
        "concerned": {"mouthFrown.L": 1.0, "mouthFrown.R": 1.0, "browInnerUp": 1.0},
        "curious": {"browOuterUp.L": 1.0, "browDown.R": 0.6},
    }.items():
        set_shapes(**values)
        shot(RENDER_DIR / f"expression-{name}.png", (0.0, -0.55, 1.65), (0.0, 0.0, 1.645), 85)
    set_shapes()
    scene.render.resolution_x = 900
    shot(RENDER_DIR / "three-quarter.png", (-0.9, -1.25, 1.55), (0.0, 0.0, 1.45), 50)


def export(armature, face, body, orb):
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_PATH))
    armature.data.pose_position = "POSE"
    FBX_PATH.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in (armature, face, body, orb):
        obj.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.export_scene.fbx(
        filepath=str(FBX_PATH),
        use_selection=True,
        object_types={"ARMATURE", "MESH"},
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_UNITS",
        axis_forward="-Z",
        axis_up="Y",
        add_leaf_bones=False,
        use_armature_deform_only=False,
        use_mesh_modifiers=False,  # Keeps shape keys; skinning is exported separately.
        mesh_smooth_type="FACE",
        path_mode="STRIP",
        bake_anim=True,
        bake_anim_use_all_actions=True,
        bake_anim_use_nla_strips=False,
        bake_anim_use_all_bones=True,
        bake_anim_force_startend_keying=True,
    )
    triangles = sum(len(p.vertices) - 2 for obj in (face, body, orb) for p in obj.data.polygons)
    log(f"COOLMAN_BUILD_COMPLETE fbx={FBX_PATH} triangles={triangles} faceShapes={len(face.data.shape_keys.key_blocks) - 1}")


def main():
    armature = import_source()
    clean_rig(armature)
    export_textures()
    face, body = merge_meshes(armature)
    add_head_bones(armature, face)
    rig, failed = build_shape_keys(face)
    if failed:
        raise SystemExit(f"COOLMAN_CHECKS_FAILED: {failed}")
    orb = add_status_orb(armature, body)
    if "--no-render" not in sys.argv:
        render_reviews(armature, face)
    export(armature, face, body, orb)
    log(f"meshes: face verts={len(face.data.vertices)} materials={[m.name for m in face.data.materials]}; "
        f"body verts={len(body.data.vertices)} materials={[m.name for m in body.data.materials]}")


main()
