bl_info = {
    "name": "Dungeon Colonizer",
    "author": "Aidan Ault",
    "version": (0, 3, 2),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Utility > Dungeon Colonizer",
    "description": "Generate labyrinthine 3D tunnels inside a closed mesh using Space Colonization + Skin/Remesh.",
    "category": "Object",
}

import bpy
import bmesh
import random
import math
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from bpy.props import (
    PointerProperty,
    BoolProperty,
    IntProperty,
    FloatProperty,
    EnumProperty,
    StringProperty,
)

# ============================================================
# Spatial Hash (3D grid buckets)
# ============================================================

class SpatialHash3D:
    """Simple spatial hash for radius queries (accelerates nearest / neighborhood lookups)."""
    __slots__ = ("bucket_size", "inv", "cells")

    def __init__(self, bucket_size: float):
        self.bucket_size = max(1e-9, bucket_size)
        self.inv = 1.0 / self.bucket_size
        self.cells = {}

    def _key(self, p: Vector):
        return (
            int(math.floor(p.x * self.inv)),
            int(math.floor(p.y * self.inv)),
            int(math.floor(p.z * self.inv)),
        )

    def insert(self, idx: int, p: Vector):
        k = self._key(p)
        self.cells.setdefault(k, []).append(idx)

    def rebuild(self, points: list[Vector]):
        self.cells.clear()
        for i, p in enumerate(points):
            self.insert(i, p)

    def query_radius(self, p: Vector, r: float) -> list[int]:
        if r <= 0.0:
            return []
        rr = r * self.inv
        kx, ky, kz = self._key(p)
        dr = int(math.ceil(rr))
        out = []
        for ix in range(kx - dr, kx + dr + 1):
            for iy in range(ky - dr, ky + dr + 1):
                for iz in range(kz - dr, kz + dr + 1):
                    bucket = self.cells.get((ix, iy, iz))
                    if bucket:
                        out.extend(bucket)
        return out


# ============================================================
# Volume sampling helpers
# ============================================================

def _build_bvh_from_object(obj: bpy.types.Object) -> BVHTree:
    """Build a BVH in world space for robust inside-volume tests."""
    dg = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(dg)
    mesh = eval_obj.to_mesh()

    verts_world = [eval_obj.matrix_world @ v.co for v in mesh.vertices]
    polys = [p.vertices[:] for p in mesh.polygons]

    bvh = BVHTree.FromPolygons(verts_world, polys, all_triangles=False)
    eval_obj.to_mesh_clear()
    return bvh


def _point_inside_mesh_bvh(point: Vector, bvh: BVHTree) -> bool:
    """Parity ray cast along +X. Odd intersections => inside."""
    direction = Vector((1.0, 0.0, 0.0))
    max_dist = 1.0e9
    hits = 0
    origin = point.copy()

    eps = 1e-5
    for _ in range(128):
        loc, normal, index, dist = bvh.ray_cast(origin, direction, max_dist)
        if loc is None:
            break
        hits += 1
        origin = loc + direction * eps

    return (hits % 2) == 1


def _grid_points_in_volume(obj: bpy.types.Object, spacing: float) -> list[Vector]:
    """Fill bounding box with cubic lattice, cull points outside true closed mesh volume."""
    spacing = max(1e-9, spacing)
    bvh = _build_bvh_from_object(obj)

    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    min_v = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
    max_v = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))

    pts = []
    x = min_v.x
    while x <= max_v.x + 1e-9:
        y = min_v.y
        while y <= max_v.y + 1e-9:
            z = min_v.z
            while z <= max_v.z + 1e-9:
                p = Vector((x, y, z))
                if _point_inside_mesh_bvh(p, bvh):
                    pts.append(p)
                z += spacing
            y += spacing
        x += spacing
    return pts


# ============================================================
# Output creation
# ============================================================

def _make_nodes_object(name: str, points: list[Vector]) -> bpy.types.Object:
    """Create a mesh object containing only vertices (node cloud visualization)."""
    mesh = bpy.data.meshes.new(name + "_NodesMesh")
    obj = bpy.data.objects.new(name + "_Nodes", mesh)
    bpy.context.collection.objects.link(obj)
    mesh.from_pydata([p.to_tuple() for p in points], [], [])
    mesh.update()
    return obj


def _make_skeleton_mesh(name: str, nodes: list[Vector], edges: list[tuple[int, int]]) -> bpy.types.Object:
    """Create the colonization skeleton as a mesh (verts+edges)."""
    mesh = bpy.data.meshes.new(name + "_SkeletonMesh")
    obj = bpy.data.objects.new(name + "_Skeleton", mesh)
    bpy.context.collection.objects.link(obj)
    mesh.from_pydata([p.to_tuple() for p in nodes], edges, [])
    mesh.update()
    return obj


def _skeleton_to_curve(skel_obj: bpy.types.Object, curve_name: str) -> bpy.types.Object:
    """Convert skeleton mesh to a curve object (for editing/visualization)."""
    bpy.ops.object.select_all(action='DESELECT')
    skel_obj.select_set(True)
    bpy.context.view_layer.objects.active = skel_obj
    bpy.ops.object.duplicate()
    dup = bpy.context.view_layer.objects.active
    dup.name = curve_name + "_Src"

    bpy.ops.object.select_all(action='DESELECT')
    dup.select_set(True)
    bpy.context.view_layer.objects.active = dup
    bpy.ops.object.convert(target='CURVE')
    curve_obj = bpy.context.view_layer.objects.active
    curve_obj.name = curve_name
    curve_obj.data.name = curve_name + "_Data"
    return curve_obj


def _ensure_skin_and_set_radius_bmesh(obj: bpy.types.Object, radius: float):
    """
    FAST + STABLE: set skin radii via BMesh skin layer (no Edit Mode ops).
    Also ensures a Skin modifier exists.
    """
    radius = max(0.0, radius)

    # Ensure Skin modifier exists
    skin_mod = obj.modifiers.get("DC_Skin")
    if skin_mod is None:
        skin_mod = obj.modifiers.new("DC_Skin", type='SKIN')
    skin_mod.use_smooth_shade = True

    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    skin_layer = bm.verts.layers.skin.verify()
    for v in bm.verts:
        sv = v[skin_layer]
        sv.radius = (radius, radius)
    bm.to_mesh(me)
    bm.free()
    me.update()


def _apply_modifier(obj: bpy.types.Object, mod_name: str):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.modifier_apply(modifier=mod_name)
    except RuntimeError:
        pass


def _apply_smoothing(obj: bpy.types.Object, mode: str, factor: float = 0.5, iterations: int = 5):
    """mode: 'NONE' | 'SMOOTH' | 'LAPLACIAN'"""
    if mode == 'NONE':
        return

    if mode == 'SMOOTH':
        m = obj.modifiers.new("DC_Smooth", type='SMOOTH')
        m.factor = max(0.0, min(1.0, factor))
        m.iterations = max(0, iterations)
        _apply_modifier(obj, m.name)
        return

    if mode == 'LAPLACIAN':
        m = obj.modifiers.new("DC_LaplacianSmooth", type='LAPLACIANSMOOTH')
        m.iterations = max(0, iterations)
        m.lambda_factor = 1.0
        m.lambda_border = 1.0
        m.use_volume_preserve = True
        _apply_modifier(obj, m.name)
        return


def _apply_voxel_remesh(obj: bpy.types.Object, voxel_size: float, adaptivity: float = 0.0):
    """Voxel remesh via Remesh modifier (VOXEL mode)."""
    voxel_size = max(1e-6, voxel_size)
    m = obj.modifiers.new("DC_VoxelRemesh", type='REMESH')
    m.mode = 'VOXEL'
    m.voxel_size = voxel_size
    m.adaptivity = max(0.0, min(1.0, adaptivity))
    m.use_smooth_shade = True
    _apply_modifier(obj, m.name)


def _apply_quadriflow_remesh(obj: bpy.types.Object, target_faces: int):
    """QuadriFlow remesh operator (slow, but face-count driven)."""
    target_faces = max(100, int(target_faces))

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.object.quadriflow_remesh(
        use_paint_symmetry=False,
        use_preserve_sharp=True,
        use_preserve_boundary=True,
        use_mesh_curvature=True,
        use_preserve_volume=True,
        target_faces=target_faces,
        seed=0
    )


# ============================================================
# Degree limiting (max 4, rare)
# ============================================================

def _degree_accept_prob(deg: int) -> float:
    """
    Make 3-way and 4-way nodes rare.
    """
    if deg <= 1:
        return 1.0
    if deg == 2:
        return 0.55
    if deg == 3:
        return 0.10
    return 0.0  # deg >= 4


def _try_add_edge(edges_set: set[tuple[int, int]], edges: list[tuple[int, int]], degrees: list[int],
                  a: int, b: int, rng: random.Random) -> bool:
    if a == b:
        return False
    if a > b:
        a, b = b, a
    if (a, b) in edges_set:
        return False
    if degrees[a] >= 4 or degrees[b] >= 4:
        return False
    if rng.random() > _degree_accept_prob(degrees[a]):
        return False
    if rng.random() > _degree_accept_prob(degrees[b]):
        return False

    edges_set.add((a, b))
    edges.append((a, b))
    degrees[a] += 1
    degrees[b] += 1
    return True


# ============================================================
# Space Colonization (accelerated)
# ============================================================

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _map_slider_01_to_01_soft(x: float) -> float:
    """Map slider 0..1 into 0.1..0.9 to avoid fully extreme behavior."""
    return 0.1 + 0.8 * _clamp01(x)


def space_colonize_accel(
    attractors: list[Vector],
    rng: random.Random,
    step_size: float,
    influence_radius: float,
    kill_radius: float,
    max_iters: int,
    vertical_freq_01: float,
    connection_bias_01: float,
    allow_dead_ends: bool,
    isolation_bias_01: float,
) -> tuple[list[Vector], list[tuple[int, int]]]:

    conn = _map_slider_01_to_01_soft(connection_bias_01)
    isol = _map_slider_01_to_01_soft(isolation_bias_01)

    # Vertical Frequency:
    # 0 => very rare vertical (but not zero)
    # 1 => frequent vertical
    vf = _clamp01(vertical_freq_01)
    z_scale = 0.15 + 0.85 * (0.05 + 0.95 * vf)

    step_size = max(1e-9, step_size)
    influence_radius = max(step_size * 1.01, influence_radius)
    kill_radius = max(1e-9, kill_radius)

    attract_hash = SpatialHash3D(bucket_size=influence_radius)
    attract_hash.rebuild(attractors)
    alive = [True] * len(attractors)

    nodes: list[Vector] = [Vector((0.0, 0.0, 0.0))]
    lineage: list[int] = [0]
    next_lineage_id = 1

    degrees: list[int] = [0]
    edges_set: set[tuple[int, int]] = set()
    edges: list[tuple[int, int]] = []

    node_hash = SpatialHash3D(bucket_size=influence_radius)
    node_hash.insert(0, nodes[0])

    # Trunk
    trunk_idx = 0
    found = False
    for _ in range(20000):
        p = nodes[trunk_idx]
        for ai in attract_hash.query_radius(p, influence_radius):
            if alive[ai] and (attractors[ai] - p).length <= influence_radius:
                found = True
                break
        if found:
            break

        new_p = p + Vector((0.0, 0.0, -step_size))
        nodes.append(new_p)
        lineage.append(0)
        degrees.append(0)
        node_hash.insert(len(nodes) - 1, new_p)
        _try_add_edge(edges_set, edges, degrees, trunk_idx, len(nodes) - 1, rng)
        trunk_idx = len(nodes) - 1

    if not found:
        return nodes, edges

    for _iter in range(max_iters):
        if not any(alive):
            break

        accum = [Vector((0.0, 0.0, 0.0)) for _ in nodes]
        counts = [0 for _ in nodes]

        for ai, a in enumerate(attractors):
            if not alive[ai]:
                continue

            search_r = influence_radius
            best_i = -1
            best_d2 = 1e30

            for _expand in range(3):
                for ni in node_hash.query_radius(a, search_r):
                    d2 = (nodes[ni] - a).length_squared
                    if d2 < best_d2:
                        best_d2 = d2
                        best_i = ni
                if best_i != -1:
                    break
                search_r *= 2.0

            if best_i == -1:
                continue

            d = math.sqrt(best_d2)
            if d <= kill_radius:
                alive[ai] = False
                continue

            if d <= influence_radius and degrees[best_i] < 4:
                dir_vec = (a - nodes[best_i])
                if dir_vec.length > 1e-9:
                    dir_vec.normalize()
                    dir_vec.z *= z_scale
                    if dir_vec.length > 1e-9:
                        dir_vec.normalize()
                    accum[best_i] += dir_vec
                    counts[best_i] += 1

        growth_sites = [i for i, c in enumerate(counts) if c > 0 and degrees[i] < 4]
        if not growth_sites:
            break

        new_nodes = []
        new_edges = []
        new_lins = []

        for i in growth_sites:
            if rng.random() > _degree_accept_prob(degrees[i]):
                continue

            direction = accum[i] / float(counts[i])
            if direction.length <= 1e-9:
                continue
            direction.normalize()

            jitter = Vector((
                rng.uniform(-0.15, 0.15),
                rng.uniform(-0.15, 0.15),
                rng.uniform(-0.06, 0.06),
            ))
            direction = direction + jitter * 0.30
            if direction.length > 1e-9:
                direction.normalize()

            p_new = nodes[i] + direction * step_size

            lin = lineage[i]
            if degrees[i] >= 2 and rng.random() < (0.12 * (1.0 - isol)):
                lin = next_lineage_id
                next_lineage_id += 1

            new_edges.append((i, len(nodes) + len(new_nodes)))
            new_nodes.append(p_new)
            new_lins.append(lin)

        if not new_nodes:
            break

        # Merge close nodes
        merge_thresh2 = (step_size * 0.25) ** 2
        new_to_final = []
        for idx_local, p in enumerate(new_nodes):
            merged = -1
            for ni in node_hash.query_radius(p, step_size * 0.5):
                if (nodes[ni] - p).length_squared <= merge_thresh2:
                    merged = ni
                    break
            if merged >= 0:
                new_to_final.append(merged)
            else:
                nodes.append(p)
                lineage.append(new_lins[idx_local])
                degrees.append(0)
                ni = len(nodes) - 1
                node_hash.insert(ni, p)
                new_to_final.append(ni)

        # Add growth edges (degree limited)
        for (parent_i, _tmp), child_i in zip(new_edges, new_to_final):
            _try_add_edge(edges_set, edges, degrees, parent_i, child_i, rng)

        # Interconnections (loops) (degree limited)
        connect_dist = step_size * 1.5
        connect_dist2 = connect_dist * connect_dist
        touched = set(new_to_final)

        for i in touched:
            if degrees[i] >= 4:
                continue
            if rng.random() > conn:
                continue

            pi = nodes[i]
            cand = node_hash.query_radius(pi, connect_dist)
            if not cand:
                continue

            lin_i = lineage[i]
            best_j = -1
            best_score = -1e30

            for j in cand:
                if j == i or degrees[j] >= 4:
                    continue
                d2 = (nodes[j] - pi).length_squared
                if d2 > connect_dist2:
                    continue
                same = 1.0 if lineage[j] == lin_i else 0.0
                score = -d2 + same * (2.0 * isol) - (1.0 - same) * (2.0 * isol)
                score += rng.uniform(-0.05, 0.05)
                if score > best_score:
                    best_score = score
                    best_j = j

            if best_j >= 0:
                cross = lineage[best_j] != lin_i
                if cross and rng.random() < (0.65 * isol):
                    continue
                _try_add_edge(edges_set, edges, degrees, i, best_j, rng)

        # Dead-end reduction (degree limited)
        if not allow_dead_ends and (_iter % 8 == 0):
            endpoints = [idx for idx, deg in enumerate(degrees) if deg == 1]
            end_dist = step_size * 1.75
            end_dist2 = end_dist * end_dist
            for i in endpoints[:min(2500, len(endpoints))]:
                if degrees[i] >= 4:
                    continue
                pi = nodes[i]
                best_j = -1
                best_d2 = 1e30
                for j in node_hash.query_radius(pi, end_dist):
                    if j == i or degrees[j] >= 4:
                        continue
                    d2 = (nodes[j] - pi).length_squared
                    if d2 <= end_dist2 and d2 < best_d2:
                        best_d2 = d2
                        best_j = j
                if best_j >= 0:
                    _try_add_edge(edges_set, edges, degrees, i, best_j, rng)

    return nodes, edges


# ============================================================
# Properties
# ============================================================

class DC_Properties(bpy.types.PropertyGroup):
    target_obj: PointerProperty(
        name="Volume Object",
        type=bpy.types.Object,
        description="Closed mesh object to colonize. Nodes are generated inside its true volume.",
        poll=lambda self, obj: (obj is not None and obj.type == 'MESH')
    )

    node_spacing: FloatProperty(
        name="Node Spacing",
        description="Distance between points in the internal cubic node lattice. Smaller = denser attractors (slower).",
        default=0.10,
        min=0.001,
        max=10.0,
        subtype='DISTANCE',
        unit='LENGTH',
    )

    seed: IntProperty(
        name="Seed",
        description="Deterministic seed value used when Random Seed is disabled.",
        default=1,
        min=0,
        max=2**31 - 1
    )

    use_random_seed: BoolProperty(
        name="Random Seed",
        description="If enabled, a new random seed is used every run. Used Seed is shown below for reproducibility.",
        default=True
    )

    last_seed_used: IntProperty(
        name="Used Seed",
        description="The actual seed used on the most recent run.",
        default=0,
        min=0,
        max=2**31 - 1,
        options=set(),
    )

    gen_id: StringProperty(
        name="GenID",
        description="Shareable generation ID string (seed + key slider values).",
        default="GenID: -",
        options=set(),
    )

    max_iters: IntProperty(
        name="Max Iterations",
        description="Maximum growth iterations. Higher fills more but is slower.",
        default=500,
        min=1,
        max=100000
    )

    influence_mult: FloatProperty(
        name="Influence Radius",
        description="Attractor influence radius as a multiplier of Node Spacing.",
        default=2.5,
        min=1.1,
        max=10.0
    )

    kill_mult: FloatProperty(
        name="Kill Radius",
        description="Attractor kill radius as a multiplier of Node Spacing. Attractors within this distance are consumed.",
        default=0.8,
        min=0.1,
        max=5.0
    )

    vertical_frequency: FloatProperty(
        name="Vertical Frequency",
        description="How often tunnels climb/descend. 0 = very rare up/down, 1 = frequent up/down.",
        default=0.25,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )

    connection_bias: FloatProperty(
        name="Connection Bias",
        description="How often separate tunnels connect into loops. 0 = rare, 1 = common (never absolute).",
        default=0.35,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )

    isolation_bias: FloatProperty(
        name="Isolation Bias",
        description="How strongly branches stay within their own lineage. 0 = interwoven, 1 = separated.",
        default=0.55,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )

    allow_dead_ends: BoolProperty(
        name="Allow Dead-Ends",
        description="If disabled, the generator periodically tries to connect endpoints to reduce dead-ends (degree limited).",
        default=True
    )

    out_mesh: BoolProperty(
        name="Tunnel Mesh",
        description="Output the final 3D tunnel mesh (Skin, optionally remeshed/smoothed).",
        default=True
    )

    out_curve: BoolProperty(
        name="Curve Skeleton",
        description="Output a curve representing the tunnel skeleton.",
        default=False
    )

    out_nodes: BoolProperty(
        name="Node Cloud",
        description="Output the attractor node cloud used for colonization (debug).",
        default=False
    )

    tunnel_radius: FloatProperty(
        name="Tunnel Radius",
        description="Tube radius used by the Skin modifier (distance from skeleton edges to tunnel wall).",
        default=0.02,
        min=0.0,
        max=10.0,
        subtype='DISTANCE',
        unit='LENGTH',
    )

    apply_manifold_remesh: BoolProperty(
        name="Make Manifold",
        description="If enabled, remesh the skinned tubes into a single cohesive manifold-like mesh (heavier).",
        default=True
    )

    remesh_mode: EnumProperty(
        name="Remesh Definition",
        description="Choose how to remesh: None (fast), Voxel (solid/organic), or QuadriFlow (slow).",
        items=[
            ('NONE', "None", "No remesh. Skin output only (fastest)."),
            ('VOXEL', "Voxel Size", "Voxel remesh: set voxel size (fast, organic)."),
            ('FACE_COUNT', "Target Faces", "QuadriFlow: set target face count (slow)."),
        ],
        default='VOXEL'
    )

    auto_voxel: BoolProperty(
        name="Auto Voxel Size",
        description="If enabled, voxel size is computed from Tunnel Radius to reduce fragmentation/crashes.",
        default=True
    )

    voxel_mult: FloatProperty(
        name="Voxel Mult",
        description="When Auto Voxel Size is ON: voxel_size = max(1e-6, tunnel_radius * voxel_mult). Typical: 0.35–0.8",
        default=0.6,
        min=0.1,
        max=2.0
    )

    voxel_size: FloatProperty(
        name="Voxel Size",
        description="Manual voxel size for Voxel Remesh (used only when Auto Voxel Size is OFF).",
        default=0.05,
        min=0.000001,
        max=10.0,
        subtype='DISTANCE',
        unit='LENGTH',
    )

    target_faces: IntProperty(
        name="Target Faces",
        description="Approximate max face count for QuadriFlow remesh (slow).",
        default=20000,
        min=100,
        max=10_000_000
    )

    smoothing_mode: EnumProperty(
        name="Smoothing",
        description="Optional post-smoothing applied after Skin/Remesh.",
        items=[
            ('NONE', "None", "No smoothing."),
            ('SMOOTH', "Smooth", "Simple smoothing modifier."),
            ('LAPLACIAN', "Laplacian", "Laplacian smooth (often preserves volume better)."),
        ],
        default='NONE'
    )

    smoothing_iterations: IntProperty(
        name="Smooth Iterations",
        description="Number of smoothing iterations.",
        default=5,
        min=0,
        max=200
    )

    smoothing_factor: FloatProperty(
        name="Smooth Factor",
        description="Smoothing strength (Smooth modifier only).",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )


def _update_gen_id(props: DC_Properties, seed_used: int):
    v = f"{props.vertical_frequency:.2f}"
    c = f"{props.connection_bias:.2f}"
    i = f"{props.isolation_bias:.2f}"
    props.gen_id = f"GenID: {seed_used}-{v}-{c}-{i}"


# ============================================================
# Operators
# ============================================================

class DC_OT_reseed(bpy.types.Operator):
    bl_idname = "dc.reseed"
    bl_label = "Reseed"
    bl_description = "Generate a new Seed value (and update the GenID preview)."

    def execute(self, context):
        props = context.scene.dc_props
        new_seed = random.randint(0, 2**31 - 1)
        props.seed = new_seed
        props.last_seed_used = new_seed
        _update_gen_id(props, new_seed)
        self.report({'INFO'}, f"Reseeded: {new_seed}")
        return {'FINISHED'}


class DC_OT_colonize(bpy.types.Operator):
    bl_idname = "dc.colonize_dungeon"
    bl_label = "Colonize The Dungeon"
    bl_description = "Run space colonization inside the selected volume and output tunnels/nodes/curve as enabled."
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.dc_props

        if not (props.out_mesh or props.out_curve or props.out_nodes):
            self.report({'WARNING'}, "No output activated")
            return {'CANCELLED'}

        if props.target_obj is None or props.target_obj.type != 'MESH':
            self.report({'ERROR'}, "Please select a closed Mesh Volume Object")
            return {'CANCELLED'}

        seed_used = random.randint(0, 2**31 - 1) if props.use_random_seed else int(props.seed)
        props.last_seed_used = seed_used
        _update_gen_id(props, seed_used)
        rng = random.Random(seed_used)

        try:
            attractors = _grid_points_in_volume(props.target_obj, props.node_spacing)
        except Exception as e:
            self.report({'ERROR'}, f"Failed volume sampling: {e}")
            return {'CANCELLED'}

        if not attractors:
            self.report({'WARNING'}, "Volume Too Small")
            return {'CANCELLED'}

        step_size = props.node_spacing
        influence_r = step_size * props.influence_mult
        kill_r = step_size * props.kill_mult

        nodes, edges = space_colonize_accel(
            attractors=attractors,
            rng=rng,
            step_size=step_size,
            influence_radius=influence_r,
            kill_radius=kill_r,
            max_iters=props.max_iters,
            vertical_freq_01=props.vertical_frequency,
            connection_bias_01=props.connection_bias,
            allow_dead_ends=props.allow_dead_ends,
            isolation_bias_01=props.isolation_bias,
        )

        base_name = "DungeonColonizer"
        created = []

        if props.out_nodes:
            created.append(_make_nodes_object(base_name, attractors))

        skel_obj = _make_skeleton_mesh(base_name, nodes, edges)

        if props.out_curve:
            curve_obj = _skeleton_to_curve(skel_obj, base_name + "_Curve")
            created.append(curve_obj)

        if props.out_mesh:
            # Duplicate skeleton for tube building
            bpy.ops.object.select_all(action='DESELECT')
            skel_obj.select_set(True)
            bpy.context.view_layer.objects.active = skel_obj
            bpy.ops.object.duplicate()
            tube_src = bpy.context.view_layer.objects.active
            tube_src.name = base_name + "_TubeSrc"

            # Skin radius (stable)
            _ensure_skin_and_set_radius_bmesh(tube_src, props.tunnel_radius)

            # Apply skin so remesh/smoothing operate on real geometry
            _apply_modifier(tube_src, "DC_Skin")

            # Safety guard against voxel crashes on huge graphs
            # (You can loosen this later—this is just to stop hard-crashes.)
            if props.apply_manifold_remesh and props.remesh_mode == 'VOXEL':
                if len(nodes) > 25000 or len(edges) > 40000:
                    self.report({'WARNING'}, "Skeleton is very large; voxel remesh skipped to avoid crash. Try 'None' remesh or lower Node Spacing/Iterations.")
                    # Skip voxel remesh
                else:
                    if props.auto_voxel:
                        vsize = max(1e-6, props.tunnel_radius * props.voxel_mult)
                    else:
                        vsize = props.voxel_size
                    _apply_voxel_remesh(tube_src, vsize, adaptivity=0.0)

            elif props.apply_manifold_remesh and props.remesh_mode == 'FACE_COUNT':
                _apply_quadriflow_remesh(tube_src, props.target_faces)

            # Post smoothing
            _apply_smoothing(
                tube_src,
                props.smoothing_mode,
                factor=props.smoothing_factor,
                iterations=props.smoothing_iterations
            )

            tube_src.name = base_name + "_TunnelMesh"
            created.append(tube_src)

            bpy.data.objects.remove(skel_obj, do_unlink=True)
        else:
            if not props.out_curve:
                skel_obj.name = base_name + "_Skeleton"
                created.append(skel_obj)
            else:
                bpy.data.objects.remove(skel_obj, do_unlink=True)

        bpy.ops.object.select_all(action='DESELECT')
        for o in created:
            if o and o.name in bpy.data.objects:
                o.select_set(True)
        if created:
            bpy.context.view_layer.objects.active = created[-1]

        self.report({'INFO'}, f"{props.gen_id} | Attractors:{len(attractors)} Nodes:{len(nodes)} Edges:{len(edges)}")
        return {'FINISHED'}


# ============================================================
# UI Panel
# ============================================================

class DC_PT_panel(bpy.types.Panel):
    bl_label = "Dungeon Colonizer"
    bl_idname = "DC_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "COLONIZER"  # change this string to rename the N-panel tab

    def draw(self, context):
        layout = self.layout
        props = context.scene.dc_props

        col = layout.column(align=True)

        col.label(text="Volume")
        col.prop(props, "target_obj")

        col.separator()
        col.label(text="Node Fill")
        col.prop(props, "node_spacing")

        col.separator()
        col.label(text="Randomness")
        col.prop(props, "use_random_seed")
        seed_row = col.row(align=True)
        seed_row.prop(props, "seed")
        seed_row.operator("dc.reseed", text="", icon='FILE_REFRESH')

        info = col.box()
        info.label(text=f"Used Seed: {props.last_seed_used}")
        info.label(text=props.gen_id)

        col.separator()
        col.label(text="Colonization")
        col.prop(props, "max_iters")
        col.prop(props, "influence_mult")
        col.prop(props, "kill_mult")
        col.prop(props, "vertical_frequency")
        col.prop(props, "connection_bias")
        col.prop(props, "isolation_bias")
        col.prop(props, "allow_dead_ends")

        col.separator()
        col.label(text="Outputs")
        row = col.row(align=True)
        row.prop(props, "out_mesh")
        row.prop(props, "out_curve")
        row.prop(props, "out_nodes")

        col.separator()
        col.label(text="Tunnel Volume")
        col.prop(props, "tunnel_radius")
        col.prop(props, "apply_manifold_remesh")

        if props.apply_manifold_remesh:
            col.prop(props, "remesh_mode")
            if props.remesh_mode == 'VOXEL':
                col.prop(props, "auto_voxel")
                if props.auto_voxel:
                    col.prop(props, "voxel_mult")
                else:
                    col.prop(props, "voxel_size")
            elif props.remesh_mode == 'FACE_COUNT':
                col.prop(props, "target_faces")

        col.separator()
        col.label(text="Smoothing")
        col.prop(props, "smoothing_mode")
        if props.smoothing_mode != 'NONE':
            col.prop(props, "smoothing_iterations")
            if props.smoothing_mode == 'SMOOTH':
                col.prop(props, "smoothing_factor")

        col.separator()
        col.operator("dc.colonize_dungeon", text="Colonize The Dungeon", icon='MOD_PARTICLES')


# ============================================================
# Registration
# ============================================================

classes = (
    DC_Properties,
    DC_OT_reseed,
    DC_OT_colonize,
    DC_PT_panel,
)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.dc_props = PointerProperty(type=DC_Properties)

def unregister():
    if hasattr(bpy.types.Scene, "dc_props"):
        del bpy.types.Scene.dc_props
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

if __name__ == "__main__":
    register()
