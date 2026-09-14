"""Builds Milo v3: a soft clay, Shin-chan-inspired rigged character with an original identity.

Run from the Android assistant repository root:

    blender --background --python tools/blender/build_milo_character.py

Outputs the editable .blend, the Unity .fbx, and review renders. The character is three
skinned meshes (body, face, hair) plus the rigid status orb. Colours are vertex colours;
the alpha channel carries a shading class read by the Unity MiloClayToon shader.
"""

from pathlib import Path
import math

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


ROOT = Path(__file__).resolve().parents[2]
ART_DIR = ROOT / "unity" / "AvatarPrototype" / "Assets" / "AvatarPrototype" / "Resources" / "Character"
RENDER_DIR = ROOT / "design" / "renders"
BLEND_PATH = ART_DIR / "MiloRig.blend"
FBX_PATH = ART_DIR / "MiloRig.fbx"

# Alpha shading classes: skin, cloth, hair (glossy), dark detail, bright detail.
SKIN, CLOTH, HAIR, DARK, BRIGHT = 1.0, 0.75, 0.5, 0.25, 0.0

C_SKIN = (1.00, 0.80, 0.68)
C_SKIN_SHADE = (0.96, 0.70, 0.60)
C_BLUSH = (0.98, 0.62, 0.60)
C_HAIR = (0.045, 0.040, 0.045)
C_SHIRT = (0.86, 0.10, 0.12)
C_SHIRT_RIB = (0.70, 0.06, 0.09)
C_SHORTS = (0.30, 0.28, 0.72)
C_SOCK = (0.97, 0.96, 0.93)
C_SHOE = (0.98, 0.74, 0.14)
C_SOLE = (0.90, 0.62, 0.10)
C_WHITE = (0.99, 0.98, 0.96)
C_PUPIL = (0.035, 0.030, 0.035)
C_LINE = (0.16, 0.08, 0.07)
C_MOUTH = (0.36, 0.07, 0.09)
C_LIP = (0.94, 0.66, 0.58)
C_TONGUE = (0.93, 0.44, 0.48)
C_TEETH = (0.99, 0.97, 0.93)
C_STATUS = (0.10, 0.88, 0.55)

HEAD_CENTER = Vector((0.0, 0.0, 2.68))
HEAD_RADII = Vector((0.93, 0.86, 0.98))
HZ = HEAD_CENTER.z  # Face features are placed relative to the head centre.
EYE_Z = HZ + 0.04

BONES = {
    "Root": ((0, 0, 0), (0, 0, 0.25), None),
    "Hips": ((0, 0, 0.66), (0, 0, 0.98), "Root"),
    "Spine": ((0, 0, 0.98), (0, 0, 1.36), "Hips"),
    "Chest": ((0, 0, 1.36), (0, 0, 1.78), "Spine"),
    "Neck": ((0, 0, 1.78), (0, 0, 1.92), "Chest"),
    "Head": ((0, 0, 1.92), (0, 0, 3.60), "Neck"),
    "Jaw": ((0, -0.30, HZ - 0.30), (0, -0.62, HZ - 0.48), "Head"),
    "Eye.L": ((-0.27, -0.55, EYE_Z), (-0.27, -0.85, EYE_Z), "Head"),
    "Eye.R": ((0.27, -0.55, EYE_Z), (0.27, -0.85, EYE_Z), "Head"),
    "UpperArm.L": ((-0.56, 0, 1.70), (-0.74, -0.02, 1.46), "Chest"),
    "LowerArm.L": ((-0.74, -0.02, 1.46), (-0.82, -0.05, 1.22), "UpperArm.L"),
    "Hand.L": ((-0.82, -0.05, 1.22), (-0.85, -0.07, 1.06), "LowerArm.L"),
    "UpperArm.R": ((0.56, 0, 1.70), (0.74, -0.02, 1.46), "Chest"),
    "LowerArm.R": ((0.74, -0.02, 1.46), (0.82, -0.05, 1.22), "UpperArm.R"),
    "Hand.R": ((0.82, -0.05, 1.22), (0.85, -0.07, 1.06), "LowerArm.R"),
    "UpperLeg.L": ((-0.25, 0, 0.74), (-0.25, 0, 0.44), "Hips"),
    "LowerLeg.L": ((-0.25, 0, 0.44), (-0.25, 0, 0.18), "UpperLeg.L"),
    "Foot.L": ((-0.25, 0, 0.18), (-0.25, -0.28, 0.08), "LowerLeg.L"),
    "UpperLeg.R": ((0.25, 0, 0.74), (0.25, 0, 0.44), "Hips"),
    "LowerLeg.R": ((0.25, 0, 0.44), (0.25, 0, 0.18), "UpperLeg.R"),
    "Foot.R": ((0.25, 0, 0.18), (0.25, -0.28, 0.08), "LowerLeg.R"),
}


def smoothstep(edge0, edge1, value):
    t = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------------------
# Scene and rig


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def create_rig():
    data = bpy.data.armatures.new("MiloRigArmature")
    rig = bpy.data.objects.new("MiloRig", data)
    bpy.context.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for name, (head, tail, parent) in BONES.items():
        bone = data.edit_bones.new(name)
        bone.head = head
        bone.tail = tail
        if parent:
            bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


def segment_distance(point, head, tail):
    head, tail = Vector(head), Vector(tail)
    axis = tail - head
    t = max(0.0, min(1.0, (point - head).dot(axis) / max(axis.length_squared, 1e-9)))
    return (point - (head + axis * t)).length


# ---------------------------------------------------------------------------
# Mesh assembly helpers. Parts are plain vertex/face lists so shape keys can be
# regenerated with identical topology.


class Part:
    def __init__(self, verts, faces, color, shade, bones, outward_from=None):
        self.verts = [Vector(v) for v in verts]
        self.faces = orient_outward(self.verts, faces, outward_from)
        self.color = color
        self.shade = shade
        self.bones = bones  # List of candidate bone names for smooth weights.


def orient_outward(verts, faces, reference=None):
    """Unity culls back faces, so every part is wound to face away from its centre (or the head)."""
    if not faces:
        return faces
    if reference is None:
        reference = sum(verts, Vector()) / len(verts)
    score = 0.0
    for face in faces:
        a, b, c = verts[face[0]], verts[face[1]], verts[face[2]]
        normal = (b - a).cross(c - a)
        center = (a + b + c) / 3
        score += normal.dot(center - reference)
    return faces if score >= 0 else [tuple(reversed(face)) for face in faces]


def mesh_object(name, parts, rig, shape_keys=None):
    verts, faces, colors, groups = [], [], [], []
    for part in parts:
        offset = len(verts)
        verts.extend(part.verts)
        faces.extend([tuple(index + offset for index in face) for face in part.faces])
        linear = tuple(channel ** 2.2 for channel in part.color)  # Float colour attributes are linear.
        colors.extend([(*linear, part.shade)] * len(part.verts))
        groups.extend([part.bones] * len(part.verts))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    mesh.validate(clean_customdata=False)
    for polygon in mesh.polygons:
        polygon.use_smooth = True

    attribute = mesh.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
    for index, color in enumerate(colors):
        attribute.data[index].color = color
    mesh.color_attributes.active_color = attribute
    mesh.color_attributes.render_color_index = 0

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(preview_material())

    vertex_groups = {bone: obj.vertex_groups.new(name=bone) for bone in BONES}
    for index, candidates in enumerate(groups):
        point = verts[index]
        if len(candidates) == 1:
            vertex_groups[candidates[0]].add([index], 1.0, "REPLACE")
            continue
        raw = []
        for bone in candidates:
            head, tail, _ = BONES[bone]
            raw.append(1.0 / max(segment_distance(point, head, tail), 0.02) ** 4)
        total = sum(raw)
        for bone, value in zip(candidates, raw):
            weight = value / total
            if weight > 0.01:
                vertex_groups[bone].add([index], weight, "REPLACE")
    for bone, group in list(vertex_groups.items()):
        if not any(bone in candidates for candidates in groups):
            obj.vertex_groups.remove(group)

    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = rig
    obj.parent = rig

    if shape_keys:
        obj.shape_key_add(name="Basis")
        for key_name, positions in shape_keys:
            key = obj.shape_key_add(name=key_name)
            key.value = 0.0
            for index, position in enumerate(positions):
                key.data[index].co = position
    return obj


_PREVIEW_MATERIAL = None


def preview_material():
    global _PREVIEW_MATERIAL
    if _PREVIEW_MATERIAL:
        return _PREVIEW_MATERIAL
    mat = bpy.data.materials.new("MiloClay")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    shader = nodes.get("Principled BSDF")
    attribute = nodes.new("ShaderNodeVertexColor")
    attribute.layer_name = "Col"
    mat.node_tree.links.new(attribute.outputs["Color"], shader.inputs["Base Color"])
    shader.inputs["Roughness"].default_value = 0.52
    shader.inputs["Subsurface Weight"].default_value = 0.12
    shader.inputs["Subsurface Radius"].default_value = (0.9, 0.35, 0.25)
    shader.inputs["Subsurface Scale"].default_value = 0.05
    shader.inputs["Coat Weight"].default_value = 0.08
    _PREVIEW_MATERIAL = mat
    return mat


def uv_sphere(segments, rings, transform):
    """Returns a sphere part whose unit-sphere points are mapped by `transform(nx, ny, nz)`."""
    verts = [transform(0.0, 0.0, 1.0)]
    for ring in range(1, rings):
        polar = math.pi * ring / rings
        for segment in range(segments):
            azimuth = 2 * math.pi * segment / segments
            verts.append(transform(math.sin(polar) * math.cos(azimuth), math.sin(polar) * math.sin(azimuth), math.cos(polar)))
    verts.append(transform(0.0, 0.0, -1.0))
    faces = []
    for segment in range(segments):
        faces.append((0, 1 + segment, 1 + (segment + 1) % segments))
    for ring in range(rings - 2):
        start = 1 + ring * segments
        for segment in range(segments):
            a = start + segment
            b = start + (segment + 1) % segments
            faces.append((a, a + segments, b + segments, b))
    bottom = len(verts) - 1
    start = 1 + (rings - 2) * segments
    for segment in range(segments):
        faces.append((start + (segment + 1) % segments, start + segment, bottom))
    return verts, faces


def ellipsoid(center, radii, segments=24, rings=14, squash=None):
    center, radii = Vector(center), Vector(radii)

    def transform(nx, ny, nz):
        point = Vector((nx * radii.x, ny * radii.y, nz * radii.z))
        if squash:
            point = squash(point, nx, ny, nz)
        return center + point

    return uv_sphere(segments, rings, transform)


def tube(points, radii, sides=16, cap=True):
    """Tube along a polyline with per-point radius; ends are rounded with small caps."""
    points = [Vector(p) for p in points]
    verts, faces = [], []
    previous_normal = None
    for index, point in enumerate(points):
        forward = (points[min(index + 1, len(points) - 1)] - points[max(index - 1, 0)]).normalized()
        if previous_normal is None:
            helper = Vector((0, 0, 1)) if abs(forward.z) < 0.9 else Vector((1, 0, 0))
            previous_normal = forward.cross(helper).normalized()
        normal = (previous_normal - forward * previous_normal.dot(forward)).normalized()
        previous_normal = normal
        binormal = forward.cross(normal)
        for side in range(sides):
            angle = 2 * math.pi * side / sides
            verts.append(point + (normal * math.cos(angle) + binormal * math.sin(angle)) * radii[index])
    for ring in range(len(points) - 1):
        for side in range(sides):
            a = ring * sides + side
            b = ring * sides + (side + 1) % sides
            faces.append((a, b, b + sides, a + sides))
    if cap:
        for end, direction in ((0, -1), (len(points) - 1, 1)):
            tip_dir = (points[end] - points[end - direction]).normalized() if len(points) > 1 else Vector((0, 0, 1))
            tip = points[end] + tip_dir * radii[end] * 0.55
            verts.append(tip)
            tip_index = len(verts) - 1
            for side in range(sides):
                a = end * sides + side
                b = end * sides + (side + 1) % sides
                faces.append((a, b, tip_index) if direction > 0 else (b, a, tip_index))
    return verts, faces


def lathe(profile, depth, segments):
    """Closed surface of revolution from (z, radius) pairs; depth scales the y axis."""
    verts, faces = [], []
    for z, radius in profile:
        for segment in range(segments):
            angle = 2 * math.pi * segment / segments
            verts.append(Vector((radius * math.cos(angle), radius * depth * math.sin(angle), z)))
    for row in range(len(profile) - 1):
        for segment in range(segments):
            a = row * segments + segment
            b = row * segments + (segment + 1) % segments
            faces.append((a, b, b + segments, a + segments))
    for index, z in ((0, profile[0][0] - 0.02), (len(profile) - 1, profile[-1][0] + 0.01)):
        verts.append(Vector((0, 0, z)))
        center = len(verts) - 1
        start = index * segments
        for segment in range(segments):
            a, b = start + segment, start + (segment + 1) % segments
            faces.append((b, a, center) if index == 0 else (a, b, center))
    return verts, faces


def ring(center, radius, depth, thickness, segments):
    points = [Vector(center) + Vector((radius * math.cos(a), radius * depth * math.sin(a), 0))
              for a in (2 * math.pi * i / segments for i in range(segments + 1))]
    return tube(points, [thickness] * len(points), 10, False)


# ---------------------------------------------------------------------------
# Head surface


def head_point(nx, ny, nz):
    """Onigiri head: as tall as it is wide, rounded crown, big cheeks bulging wide and
    forward in the lower half, and a broad rounded jaw."""
    lower = smoothstep(0.30, -0.60, nz)
    crown = smoothstep(0.35, 1.0, nz)
    front = max(0.0, -ny)
    side = min(1.0, abs(nx) * 1.8)
    dome = math.exp(-(((nz - 0.50) / 0.32) ** 2))  # Fills out the forehead into a full dome.
    x = nx * HEAD_RADII.x * (1.0 + 0.28 * lower + 0.12 * dome - 0.04 * crown)
    y = ny * HEAD_RADII.y * (1.0 + 0.15 * lower + 0.08 * dome - 0.02 * crown)
    y -= 0.16 * lower * front * (0.35 + 0.65 * side)  # Cheeks push forward.
    z = nz * HEAD_RADII.z
    z *= 1.0 - 0.16 * smoothstep(-0.50, -1.0, nz)  # Broad, soft jaw plane.
    return HEAD_CENTER + Vector((x, y, z))


class HeadSurface:
    def __init__(self, verts, faces):
        self.tree = BVHTree.FromPolygons([tuple(v) for v in verts], faces)

    def project(self, x, z, lift=0.0):
        hit, normal, _, _ = self.tree.ray_cast(Vector((x, -3.0, z)), Vector((0, 1, 0)))
        if hit is None:
            raise RuntimeError(f"Face feature at x={x:.3f}, z={z:.3f} misses the head surface")
        return hit + normal * lift, normal


# ---------------------------------------------------------------------------
# Face features (one skinned mesh with every facial blendshape)


def surface_disc(surface, center, radius_x, radius_z, lift, dome=0.0, rings=5, segments=28, shape=None):
    """Elliptical decal hugging the head. `shape(u, v)` may return a replacement (u, v)."""
    verts = []
    cx, cz = center
    for ring in range(rings + 1):
        rho = ring / rings
        count = 1 if ring == 0 else segments
        for segment in range(count):
            angle = 2 * math.pi * segment / segments
            u, v = rho * math.cos(angle), rho * math.sin(angle)
            if shape:
                u, v = shape(u, v)
            height = lift + dome * (1.0 - rho * rho)
            position, _ = surface.project(cx + u * radius_x, cz + v * radius_z, height)
            verts.append(position)
    faces = []
    for segment in range(segments):
        faces.append((0, 1 + segment, 1 + (segment + 1) % segments))
    for ring in range(1, rings):
        start = 1 + (ring - 1) * segments
        for segment in range(segments):
            a = start + segment
            b = start + (segment + 1) % segments
            faces.append((a, a + segments, b + segments, b))
    return verts, faces


def surface_strip(surface, centerline, widths, lift, thickness, across=6):
    """A raised, rounded strip following a (x, z) centreline, used for brows and lid lines."""
    verts = []
    for index, (x, z) in enumerate(centerline):
        nxt = centerline[min(index + 1, len(centerline) - 1)]
        prv = centerline[max(index - 1, 0)]
        dx, dz = nxt[0] - prv[0], nxt[1] - prv[1]
        length = math.hypot(dx, dz) or 1.0
        px, pz = -dz / length, dx / length
        for step in range(across + 1):
            s = step / across * 2 - 1
            bulge = math.sqrt(max(0.0, 1 - s * s))
            position, _ = surface.project(x + px * s * widths[index], z + pz * s * widths[index], lift + thickness[index] * bulge)
            verts.append(position)
    faces = []
    columns = across + 1
    for row in range(len(centerline) - 1):
        for step in range(across):
            a = row * columns + step
            faces.append((a, a + 1, a + columns + 1, a + columns))
    return verts, faces


MOUTH_CENTER = (0.0, HZ - 0.40)
MOUTH_SCALE = 0.78  # Small cartoon mouth; viseme widths are authored at full size.
BASE_MOUTH = dict(width=0.20, upper=0.006, lower=0.006, corner_l=0.0, corner_r=0.0, push=0.0,
                  teeth_upper=0.0, teeth_lower=0.0, tongue=0.0, tongue_front=0.0)

VISEMES = {
    "viseme_sil": {},
    "viseme_PP": dict(width=0.185, upper=-0.002, lower=-0.002, push=0.012),
    "viseme_FF": dict(width=0.21, upper=0.040, lower=-0.004, teeth_upper=0.034, push=-0.004),
    "viseme_TH": dict(width=0.20, upper=0.040, lower=0.035, teeth_upper=0.022, tongue=0.9, tongue_front=1.0),
    "viseme_DD": dict(width=0.21, upper=0.042, lower=0.050, teeth_upper=0.024, tongue=0.55),
    "viseme_kk": dict(width=0.20, upper=0.048, lower=0.075, teeth_upper=0.020, tongue=0.35),
    "viseme_CH": dict(width=0.155, upper=0.055, lower=0.060, teeth_upper=0.030, teeth_lower=0.022, push=0.030),
    "viseme_SS": dict(width=0.235, upper=0.030, lower=0.030, teeth_upper=0.028, teeth_lower=0.022),
    "viseme_nn": dict(width=0.20, upper=0.036, lower=0.050, teeth_upper=0.018, tongue=0.65),
    "viseme_RR": dict(width=0.150, upper=0.048, lower=0.058, teeth_upper=0.016, push=0.022, tongue=0.25),
    "viseme_aa": dict(width=0.215, upper=0.085, lower=0.175, teeth_upper=0.030, tongue=0.30),
    "viseme_E": dict(width=0.250, upper=0.060, lower=0.095, teeth_upper=0.030, teeth_lower=0.016, tongue=0.25),
    "viseme_ih": dict(width=0.240, upper=0.048, lower=0.068, teeth_upper=0.028, teeth_lower=0.016, tongue=0.2),
    "viseme_oh": dict(width=0.135, upper=0.090, lower=0.125, push=0.032, tongue=0.2),
    "viseme_ou": dict(width=0.090, upper=0.048, lower=0.058, push=0.048),
    "jawOpen": dict(upper=0.030, lower=0.150, teeth_upper=0.028, tongue=0.35),
    "mouthSmile.L": dict(width=0.225, corner_l=0.050),
    "mouthSmile.R": dict(width=0.225, corner_r=0.050),
    "mouthFrown.L": dict(corner_l=-0.040),
    "mouthFrown.R": dict(corner_r=-0.040),
}


def aperture(params, angle):
    """Lip opening point for an angle around the mouth centre (x right, z up)."""
    c, s = math.cos(angle), math.sin(angle)
    width = params["width"] * MOUTH_SCALE
    x = width * c
    edge = abs(c) ** 6  # Corners stay pinched while the centre opens.
    height = params["upper"] if s >= 0 else params["lower"]
    z = math.copysign(abs(s) ** 0.9, s) * height * (1.0 - 0.35 * edge)
    corner = params["corner_l"] if c < 0 else params["corner_r"]
    z += corner * edge
    return x, z


def mouth_parts(surface, params):
    cx, cz = MOUTH_CENTER
    push = params["push"]
    segments, lip_rings = 40, 5
    parts = []

    # Soft lip rim: a rounded annulus hugging the opening.
    verts = []
    for ring in range(lip_rings + 1):
        t = ring / lip_rings
        for segment in range(segments):
            angle = 2 * math.pi * segment / segments
            x, z = aperture(params, angle)
            c, s = math.cos(angle), math.sin(angle)
            lip = 0.030 + 0.012 * max(0.0, -s)  # Fuller lower lip.
            scale = 1.0 + (lip / max(params["width"], 0.05)) * t
            ox, oz = x * scale, z + math.copysign(lip * t, s if abs(s) > 1e-6 else 1.0) * abs(s)
            roll = math.sin(math.pi * t)
            position, _ = surface.project(cx + ox, cz + oz, 0.006 + 0.014 * roll * (1 - t * 0.3) + push * (1 - t))
            verts.append(position)
    faces = []
    for ring in range(lip_rings):
        for segment in range(segments):
            a = ring * segments + segment
            b = ring * segments + (segment + 1) % segments
            faces.append((a, a + segments, b + segments, b))
    parts.append(Part(verts, faces, C_LIP, SKIN, ["Head"], HEAD_CENTER))

    # Dark cavity filling the opening.
    verts, faces = [], []
    cavity_rings = 4
    verts.append(surface.project(cx, cz, 0.0058 + push)[0])
    for ring in range(1, cavity_rings + 1):
        rho = ring / cavity_rings
        for segment in range(segments):
            x, z = aperture(params, 2 * math.pi * segment / segments)
            verts.append(surface.project(cx + x * rho, cz + z * rho, 0.0058 + push)[0])
    for segment in range(segments):
        faces.append((0, 1 + (segment + 1) % segments, 1 + segment))
    for ring in range(cavity_rings - 1):
        start = 1 + ring * segments
        for segment in range(segments):
            a = start + segment
            b = start + (segment + 1) % segments
            faces.append((a, b, b + segments, a + segments))
    parts.append(Part(verts, faces, C_MOUTH, DARK, ["Head"], HEAD_CENTER))

    # Teeth rows sit just behind the lips, following the opening edge.
    for name, height_key, sign in (("upper", "teeth_upper", 1), ("lower", "teeth_lower", -1)):
        columns, rows = 16, 2
        verts, faces = [], []
        edge_height = params["upper"] if sign > 0 else params["lower"]
        visible = min(params[height_key], max(0.0, params["upper"] + params["lower"]) * 0.8)
        for row in range(rows + 1):
            for column in range(columns + 1):
                u = column / columns * 2 - 1
                angle = math.acos(u * 0.55)
                x, edge_z = aperture(params, angle if sign > 0 else -angle)
                top = edge_z * 0.9
                z = top - sign * min(visible, abs(edge_z) * 1.6) * row / rows
                verts.append(surface.project(cx + x, cz + z, 0.0085 + push)[0])
        for row in range(rows):
            for column in range(columns):
                a = row * (columns + 1) + column
                faces.append((a, a + columns + 1, a + columns + 2, a + 1) if sign > 0 else (a, a + 1, a + columns + 2, a + columns + 1))
        parts.append(Part(verts, faces, C_TEETH, BRIGHT, ["Head"], HEAD_CENTER))

    # Tongue rests in the lower opening and moves forward for "th".
    tongue_height = params["tongue"] * max(0.0, params["lower"]) * 0.55 + params["tongue_front"] * 0.012
    tongue_z = -max(0.0, params["lower"]) * 0.55 + params["tongue_front"] * 0.022
    tongue_lift = 0.0075 + push + params["tongue_front"] * 0.014
    verts, faces = surface_disc(
        surface, (cx, cz + tongue_z), params["width"] * 0.55, max(0.002, tongue_height), tongue_lift, 0.004, 3, 20
    )
    parts.append(Part(verts, faces, C_TONGUE, SKIN, ["Head"], HEAD_CENTER))
    return parts


FACE_BASE = dict(blink_l=0.0, blink_r=0.0, brow_up_l=0.0, brow_up_r=0.0, brow_down_l=0.0, brow_down_r=0.0,
                 brow_inner=0.0, cheek=0.0)
FACE_KEYS = {
    "eyeBlink.L": dict(blink_l=1.0),
    "eyeBlink.R": dict(blink_r=1.0),
    "browOuterUp.L": dict(brow_up_l=1.0),
    "browOuterUp.R": dict(brow_up_r=1.0),
    "browDown.L": dict(brow_down_l=1.0),
    "browDown.R": dict(brow_down_r=1.0),
    "browInnerUp": dict(brow_inner=1.0),
    "cheekPuff": dict(cheek=1.0),
}


def eye_parts(surface, face):
    parts = []
    for side, sign in (("L", -1), ("R", 1)):
        blink = face[f"blink_{side.lower()}"]
        cx, cz = 0.27 * sign, EYE_Z
        lid_z = cz + 0.11

        def squash(u, v, blink=blink):
            # Closing collapses the eye toward a gentle downward arc under the lid.
            target = 0.55 - 0.18 * u * u
            return u, v + (target - v) * blink * 0.97

        white_x, white_z = 0.125, 0.150
        verts, faces = surface_disc(surface, (cx, cz), white_x, white_z, 0.010, 0.030, 5, 32, squash)
        parts.append(Part(verts, faces, C_WHITE, BRIGHT, ["Head"], HEAD_CENTER))

        def pupil_shape(u, v, blink=blink):
            su, sv = u * 0.70, v * 0.74 - 0.04
            target = 0.55 - 0.18 * su * su
            return su, sv + (target - sv) * blink * 0.97

        verts, faces = surface_disc(surface, (cx, cz), white_x, white_z, 0.034, 0.030, 4, 28, pupil_shape)
        parts.append(Part(verts, faces, C_PUPIL, DARK, [f"Eye.{side}"], HEAD_CENTER))

        def glint_shape(u, v, blink=blink):
            su, sv = -0.22 + u * 0.14, 0.18 + v * 0.14
            target = 0.55 - 0.18 * su * su
            return su, sv + (target - sv) * blink * 0.97

        verts, faces = surface_disc(surface, (cx, cz), white_x, white_z, 0.046, 0.030, 2, 12, glint_shape)
        parts.append(Part(verts, faces, C_WHITE, BRIGHT, [f"Eye.{side}"], HEAD_CENTER))

        # Full dark outline around the eye; it collapses into a curved line on blink.
        points, widths, heights = [], [], []
        for index in range(41):
            angle = 2 * math.pi * index / 40 + math.pi / 2
            u, v = squash(math.cos(angle) * 1.03, math.sin(angle) * 1.03)
            points.append((cx + u * white_x, cz + v * white_z))
            upper = max(0.0, math.sin(angle))
            widths.append(0.010 + 0.008 * upper)
            heights.append(0.008 + 0.006 * upper)
        verts, faces = surface_strip(surface, points, widths, 0.030, heights, 4)
        parts.append(Part(verts, faces, C_PUPIL, DARK, ["Head"], HEAD_CENTER))

        # Thick caterpillar brow.
        up = face[f"brow_up_{side.lower()}"]
        down = face[f"brow_down_{side.lower()}"]
        inner = face["brow_inner"]
        points, widths, heights = [], [], []
        for index in range(15):
            u = index / 14  # 0 inner, 1 outer
            x = cx + sign * (-0.17 + u * 0.38)
            base = lid_z + 0.08 + 0.07 * math.sin(math.pi * (0.10 + u * 0.80))
            z = base + up * 0.06 * u - down * (0.05 + 0.05 * (1 - u)) + inner * 0.07 * (1 - u) ** 1.5
            points.append((x, z))
            fat = math.sin(math.pi * min(1.0, 0.08 + u * 0.92)) ** 0.6
            widths.append(0.034 + 0.060 * fat)
            heights.append(0.028 + 0.055 * fat)
        verts, faces = surface_strip(surface, points, widths, 0.012, heights, 8)
        parts.append(Part(verts, faces, C_HAIR, HAIR, ["Head"], HEAD_CENTER))

        # Blush.
        cheek = face["cheek"]
        verts, faces = surface_disc(surface, (0.60 * sign, HZ - 0.30), 0.15 + 0.02 * cheek, 0.085 + 0.012 * cheek, 0.004, 0.006, 3, 24)
        parts.append(Part(verts, faces, C_BLUSH, SKIN, ["Head"], HEAD_CENTER))
    return parts


def face_parts(surface, mouth, face):
    parts = mouth_parts(surface, mouth) + eye_parts(surface, face)
    nose, _ = surface.project(0.0, HZ - 0.15, -0.02)
    verts, faces = ellipsoid(nose, (0.060, 0.045, 0.045), 16, 10)
    parts.append(Part(verts, faces, C_SKIN_SHADE, SKIN, ["Head"]))
    return parts


def build_face(surface, rig):
    basis = face_parts(surface, BASE_MOUTH, FACE_BASE)
    keys = []
    for name, override in VISEMES.items():
        keys.append((name, [v for part in face_parts(surface, {**BASE_MOUTH, **override}, FACE_BASE) for v in part.verts]))
    for name, override in FACE_KEYS.items():
        keys.append((name, [v for part in face_parts(surface, BASE_MOUTH, {**FACE_BASE, **override}) for v in part.verts]))
    return mesh_object("Milo_Face", basis, rig, keys)


# ---------------------------------------------------------------------------
# Hair, head, body


def build_head_and_hair(rig):
    head_verts, head_faces = uv_sphere(72, 48, head_point)
    surface = HeadSurface(head_verts, head_faces)

    head = Part(head_verts, head_faces, C_SKIN, SKIN, ["Head"])
    ears = []
    for sign in (-1, 1):
        center = head_point(sign * 0.97, 0.10, -0.02)
        ears.append(Part(*ellipsoid(center + Vector((sign * 0.10, 0.02, 0)), (0.17, 0.11, 0.21), 24, 14), C_SKIN, SKIN, ["Head"]))
        inner = center + Vector((sign * 0.17, -0.08, 0))
        ears.append(Part(*ellipsoid(inner, (0.07, 0.03, 0.12), 16, 8), C_SKIN_SHADE, SKIN, ["Head"]))

    # Hair cap: an offset shell of the head above a hairline that is high at the
    # forehead, dips at the temples, and wraps low around the back.
    def hairline(nx, ny):
        azimuth = math.atan2(ny, nx)  # -pi/2 is the face.
        facing = math.cos(azimuth + math.pi / 2)  # 1 at the face, -1 at the back.
        return -0.40 + 0.84 * smoothstep(-0.75, 0.62, facing)

    def hair_point(nx, ny, nz):
        return HEAD_CENTER + (head_point(nx, ny, nz) - HEAD_CENTER) * 1.022 + Vector((0, 0.01, 0.015))

    # The shell grid runs from the crown (t=0) to the hairline (t=1), so the edge is smooth.
    segments, rows = 96, 28
    verts, faces = [hair_point(0.0, 0.0, 1.0)], []
    for row in range(1, rows + 1):
        t = row / rows
        for segment in range(segments):
            azimuth = 2 * math.pi * segment / segments
            ca, sa = math.cos(azimuth), math.sin(azimuth)
            nz = 1.0 + (hairline(ca, sa) - 1.0) * t
            radial = math.sqrt(max(0.0, 1.0 - nz * nz))
            point = hair_point(radial * ca, radial * sa, nz)
            if row == rows:  # Tuck the edge toward the scalp for a soft rolled rim.
                point = HEAD_CENTER + (point - HEAD_CENTER) * 0.985
            verts.append(point)
    for segment in range(segments):
        faces.append((0, 1 + segment, 1 + (segment + 1) % segments))
    for row in range(rows - 1):
        start = 1 + row * segments
        for segment in range(segments):
            a = start + segment
            b = start + (segment + 1) % segments
            faces.append((a, a + segments, b + segments, b))

    hair_obj_parts = [Part(verts, faces, C_HAIR, HAIR, ["Head"], HEAD_CENTER)]
    # Milo's signature forehead lock and small sideburn tabs.
    lock_points = [hair_point(0.08, -0.55, 0.83), hair_point(0.17, -0.64, 0.75), hair_point(0.25, -0.66, 0.70) + Vector((0, -0.01, 0)),
                   hair_point(0.30, -0.66, 0.66) + Vector((0, -0.012, 0))]
    hair_obj_parts.append(Part(*tube(lock_points, [0.055, 0.045, 0.030, 0.012], 12), C_HAIR, HAIR, ["Head"]))

    hair = mesh_object("Milo_Hair", hair_obj_parts, rig)
    solidify = hair.modifiers.new("Thickness", "SOLIDIFY")
    solidify.thickness = 0.028
    solidify.offset = 1.0
    hair.modifiers.move(hair.modifiers.find("Thickness"), 0)
    return surface, head, ears, hair


def build_body(rig, head, ears):
    parts = [head] + ears

    # Torso: a soft, slightly pear-shaped lathe in the red long-sleeve shirt.
    profile = [(0.90, 0.50), (0.96, 0.575), (1.08, 0.615), (1.28, 0.62), (1.48, 0.60), (1.64, 0.55), (1.76, 0.44), (1.84, 0.26), (1.87, 0.10)]
    parts.append(Part(*lathe(profile, 0.78, 44), C_SHIRT, CLOTH, ["Hips", "Spine", "Chest"]))
    parts.append(Part(*ring((0, 0, 0.93), 0.565, 0.78, 0.045, 56), C_SHIRT_RIB, CLOTH, ["Hips", "Spine"]))
    parts.append(Part(*ring((0, -0.01, 1.855), 0.20, 0.85, 0.036, 32), C_SHIRT_RIB, CLOTH, ["Chest"]))

    # Shorts: waist and two short legs.
    parts.append(Part(*ellipsoid((0, 0.0, 0.84), (0.57, 0.43, 0.21), 40, 18), C_SHORTS, CLOTH, ["Hips", "Spine"]))
    for sign, side in ((-1, "L"), (1, "R")):
        parts.append(Part(*tube([(sign * 0.25, 0, 0.90), (sign * 0.27, -0.01, 0.74), (sign * 0.28, -0.01, 0.62)], [0.29, 0.27, 0.25], 24), C_SHORTS, CLOTH, ["Hips", f"UpperLeg.{side}"]))

        # Legs, socks and shoes.
        parts.append(Part(*tube([(sign * 0.25, 0, 0.64), (sign * 0.25, 0, 0.44), (sign * 0.25, 0, 0.26)], [0.13, 0.12, 0.115], 20), C_SKIN, SKIN, [f"UpperLeg.{side}", f"LowerLeg.{side}"]))
        parts.append(Part(*tube([(sign * 0.25, 0, 0.29), (sign * 0.25, 0, 0.18)], [0.132, 0.13], 20), C_SOCK, CLOTH, [f"LowerLeg.{side}", f"Foot.{side}"]))

        def shoe_squash(point, nx, ny, nz):
            point.z *= 1.0 if nz > 0 else 0.55
            point.y -= 0.05 * max(0.0, -ny)
            return point

        parts.append(Part(*ellipsoid((sign * 0.26, -0.10, 0.10), (0.17, 0.27, 0.11), 28, 16, shoe_squash), C_SHOE, CLOTH, [f"Foot.{side}"]))
        parts.append(Part(*ellipsoid((sign * 0.26, -0.10, 0.035), (0.175, 0.28, 0.035), 28, 8), C_SOLE, CLOTH, [f"Foot.{side}"]))

        # Sleeves follow the arm bones; hands are soft mittens with a thumb.
        shoulder = Vector((sign * 0.44, 0.0, 1.66))
        elbow = Vector(BONES[f"LowerArm.{side}"][0])
        wrist = Vector(BONES[f"Hand.{side}"][0])
        sleeve = [shoulder, shoulder.lerp(elbow, 0.5), elbow, elbow.lerp(wrist, 0.5), wrist + (wrist - elbow).normalized() * 0.02]
        parts.append(Part(*tube(sleeve, [0.17, 0.165, 0.15, 0.14, 0.14], 20), C_SHIRT, CLOTH, ["Chest", f"UpperArm.{side}", f"LowerArm.{side}"]))
        cuff = wrist + (wrist - elbow).normalized() * 0.01
        parts.append(Part(*tube([cuff - (wrist - elbow).normalized() * 0.03, cuff + (wrist - elbow).normalized() * 0.03], [0.155, 0.155], 20), C_SHIRT_RIB, CLOTH, [f"LowerArm.{side}"]))
        hand = Vector(BONES[f"Hand.{side}"][0]).lerp(Vector(BONES[f"Hand.{side}"][1]), 0.55)
        # Round mitten-ball hands with a small thumb bump.
        parts.append(Part(*ellipsoid(hand, (0.125, 0.115, 0.13), 22, 14), C_SKIN, SKIN, [f"Hand.{side}"]))
        parts.append(Part(*ellipsoid(hand + Vector((-sign * 0.04, -0.10, 0.04)), (0.045, 0.045, 0.06), 12, 8), C_SKIN, SKIN, [f"Hand.{side}"]))

    # Four-petal assistant status pin.
    pin = Vector((0.30, -0.555, 1.55))
    for offset in ((0, 0, 0.065), (-0.065, 0, 0), (0.065, 0, 0), (0, 0, -0.065)):
        parts.append(Part(*ellipsoid(pin + Vector(offset), (0.052, 0.022, 0.060) if offset[0] == 0 else (0.060, 0.022, 0.052), 14, 8), C_WHITE, BRIGHT, ["Chest"]))
    return mesh_object("Milo_Body", parts, rig), pin


def build_status_orb(rig, pin):
    # Built around the origin so the runtime pulse scales the orb in place.
    center = pin + Vector((0, -0.03, 0))
    verts, faces = ellipsoid((0, 0, 0), (0.050, 0.024, 0.050), 20, 12)
    orb = mesh_object("Status_Orb", [Part(verts, faces, C_WHITE, BRIGHT, ["Chest"])], rig)
    orb.parent = None
    orb.location = center
    bpy.context.view_layer.update()
    # A rigid child of the chest bone rather than a fourth skinned renderer.
    orb.modifiers.clear()
    orb.vertex_groups.clear()
    world = orb.matrix_world.copy()
    orb.parent = rig
    orb.parent_type = "BONE"
    orb.parent_bone = "Chest"
    bpy.context.view_layer.update()  # Resolve the bone-relative parent before restoring placement.
    orb.matrix_world = world
    return orb


# ---------------------------------------------------------------------------
# Preview renders and export


def setup_preview():
    scene = bpy.context.scene
    world = bpy.data.worlds.new("StudioCream")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.93, 0.85, 0.78, 1.0)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.55
    scene.world = world

    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, 0))
    floor = bpy.context.object
    floor.name = "PreviewFloor"
    floor_mat = bpy.data.materials.new("PreviewFloorMat")
    floor_mat.use_nodes = True
    floor_mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.92, 0.84, 0.77, 1.0)
    floor_mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.9
    floor.data.materials.append(floor_mat)

    def area(name, location, energy, color, size, target=(0, 0, 1.9)):
        data = bpy.data.lights.new(name, "AREA")
        data.energy = energy
        data.color = color
        data.shape = "DISK"
        data.size = size
        obj = bpy.data.objects.new(name, data)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (Vector(target) - Vector(location)).to_track_quat("-Z", "Y").to_euler()
        return obj

    area("Key", (-3.5, -5.0, 5.5), 1400, (1.0, 0.93, 0.86), 5.0)
    area("Fill", (4.5, -3.5, 3.0), 500, (0.92, 0.95, 1.0), 5.0)
    area("Rim", (1.5, 4.0, 4.5), 700, (1.0, 0.95, 0.9), 3.0)

    camera_data = bpy.data.cameras.new("PreviewCamera")
    camera_data.lens = 70
    camera = bpy.data.objects.new("PreviewCamera", camera_data)
    bpy.context.collection.objects.link(camera)
    scene.camera = camera

    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 1200
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.look = "AgX - Base Contrast"
    return camera, floor


def render(camera, path, location, target, lens=70):
    camera.location = location
    camera.data.lens = lens
    camera.rotation_euler = (Vector(target) - Vector(location)).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def set_shapes(face, **values):
    for key in face.data.shape_keys.key_blocks[1:]:
        key.value = values.get(key.name, 0.0)


def save_and_export(rig, face, meshes, camera, floor):
    ART_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_PATH))

    render(camera, RENDER_DIR / "rigged-character-v3.png", (0, -11.0, 2.1), (0, 0, 1.85))
    render(camera, RENDER_DIR / "rigged-character-v3-three-quarter.png", (-7.0, -8.2, 2.8), (0, 0, 1.88))
    render(camera, RENDER_DIR / "rigged-character-v3-head.png", (-3.6, -4.6, 3.4), (0, 0, HZ - 0.1), 60)
    close = ((0, -5.4, HZ - 0.12), (0, 0, HZ - 0.12), 85)
    for name in ("viseme_sil", "viseme_PP", "viseme_FF", "viseme_TH", "viseme_aa", "viseme_E", "viseme_oh", "viseme_ou"):
        set_shapes(face, **{name: 1.0})
        render(camera, RENDER_DIR / f"v3-face-{name.split('_')[1]}.png", *close)
    set_shapes(face, **{"mouthSmile.L": 1.0, "mouthSmile.R": 1.0, "browOuterUp.L": 0.6, "browOuterUp.R": 0.6, "eyeBlink.L": 1.0})
    render(camera, RENDER_DIR / "v3-face-wink.png", *close)
    set_shapes(face)

    floor.select_set(False)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in [rig] + meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = rig
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
        colors_type="SRGB",
    )
    triangles = 0
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in meshes:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        triangles += sum(len(polygon.vertices) - 2 for polygon in mesh.polygons)
        evaluated.to_mesh_clear()
    print(f"RIG_BUILD_COMPLETE blend={BLEND_PATH} fbx={FBX_PATH} triangles={triangles} "
          f"faceShapes={len(face.data.shape_keys.key_blocks) - 1}")


def main():
    reset_scene()
    rig = create_rig()
    surface, head, ears, hair = build_head_and_hair(rig)
    face = build_face(surface, rig)
    body, pin = build_body(rig, head, ears)
    orb = build_status_orb(rig, pin)
    camera, floor = setup_preview()
    save_and_export(rig, face, [body, face, hair, orb], camera, floor)


main()
