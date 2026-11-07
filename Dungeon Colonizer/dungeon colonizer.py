bl_info = {
    "name": "Dungeon Colonizer",
    "author": "Aidan Ault",
    "version": (0, 5, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Dungeon Colonizer > Dungeon Colonizer",
    "description": "Generate labyrinthine 3D tunnels inside a closed mesh using Space Colonization + Skin/Remesh, with chambers, spawn bulges, shops, and typed node markers.",
    "category": "Object",
}

import bpy
import bmesh
import random
import math
from collections import deque
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

NODE_TYPES = {
    "mundane": "node_mundane",
    "chamber": "node_chamber",
    "shop": "node_shop",
    "trap": "node_trap",
    "enemySpawn": "node_enemySpawn",
    "playerSpawn": "node_playerSpawn",
    "nemesisSpawn": "node_nemesisSpawn",
}


# ============================================================
# Spatial Hash
# ============================================================

class SpatialHash3D:
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

    def rebuild(self, indices: list[int], points: list[Vector]):
        self.cells.clear()
        for i in indices:
            self.insert(i, points[i])

    def query_bucket_neighbors(self, p: Vector, r: float) -> list[int]:
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
# BVH helpers
# ============================================================

def _build_bvh_from_object(obj: bpy.types.Object) -> BVHTree:
    dg = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(dg)
    mesh = eval_obj.to_mesh()
    verts_world = [eval_obj.matrix_world @ v.co for v in mesh.vertices]
    polys = [p.vertices[:] for p in mesh.polygons]
    bvh = BVHTree.FromPolygons(verts_world, polys, all_triangles=False)
    eval_obj.to_mesh_clear()
    return bvh


def _build_bvh_from_mesh_object_direct(obj: bpy.types.Object) -> BVHTree | None:
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return None
    me = obj.data
    if len(me.polygons) == 0:
        return None
    verts_world = [obj.matrix_world @ v.co for v in me.vertices]
    polys = [p.vertices[:] for p in me.polygons]
    return BVHTree.FromPolygons(verts_world, polys, all_triangles=False)


def _point_inside_mesh_bvh(point: Vector, bvh: BVHTree) -> bool:
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


def _nearest_point_on_mesh_world(mesh_obj: bpy.types.Object, p_world: Vector) -> tuple[Vector | None, Vector | None]:
    if mesh_obj is None or mesh_obj.type != 'MESH' or len(mesh_obj.data.polygons) == 0:
        return None, None
    bvh = _build_bvh_from_mesh_object_direct(mesh_obj)
    if bvh is None:
        return None, None
    loc, normal, face_index, dist = bvh.find_nearest(p_world)
    return loc, normal


# ============================================================
# Volume sampling
# ============================================================

def _grid_points_in_volume(obj: bpy.types.Object, spacing: float) -> list[Vector]:
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
# Skeleton cleanup: keep largest component
# ============================================================

def _prune_skeleton_keep_largest_component(
    nodes: list[Vector],
    edges: list[tuple[int, int]],
) -> tuple[list[Vector], list[tuple[int, int]]]:

    if not edges or len(nodes) <= 1:
        return nodes, edges

    adj = [[] for _ in range(len(nodes))]
    for a, b in edges:
        if a == b:
            continue
        if 0 <= a < len(nodes) and 0 <= b < len(nodes):
            adj[a].append(b)
            adj[b].append(a)

    visited = [False] * len(nodes)
    components: list[list[int]] = []

    for i in range(len(nodes)):
        if visited[i]:
            continue
        if not adj[i]:
            visited[i] = True
            continue

        q = deque([i])
        visited[i] = True
        comp = [i]
        while q:
            u = q.popleft()
            for v in adj[u]:
                if not visited[v]:
                    visited[v] = True
                    q.append(v)
                    comp.append(v)
        components.append(comp)

    if not components:
        return nodes, edges

    components.sort(key=len, reverse=True)
    keep = set(components[0])

    old_to_new = {}
    new_nodes: list[Vector] = []
    for old_idx in sorted(keep):
        old_to_new[old_idx] = len(new_nodes)
        new_nodes.append(nodes[old_idx])

    new_edges: list[tuple[int, int]] = []
    e_set: set[tuple[int, int]] = set()
    for a, b in edges:
        if a in keep and b in keep:
            na = old_to_new[a]
            nb = old_to_new[b]
            if na == nb:
                continue
            e = (na, nb) if na < nb else (nb, na)
            if e not in e_set:
                e_set.add(e)
                new_edges.append(e)

    return new_nodes, new_edges


# ============================================================
# Remove loose mesh parts: keep largest island
# ============================================================

def _remove_loose_parts_keep_largest(mesh_obj: bpy.types.Object) -> bool:
    if mesh_obj is None or mesh_obj.type != 'MESH':
        return False
    me = mesh_obj.data
    if me is None or len(me.polygons) == 0:
        return False

    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()

    face_tag = {}
    islands = []

    def neighbors(f):
        for e in f.edges:
            for nf in e.link_faces:
                if nf is not f:
                    yield nf

    for f in bm.faces:
        if f in face_tag:
            continue
        q = deque([f])
        face_tag[f] = True
        island = [f]
        while q:
            cur = q.popleft()
            for nf in neighbors(cur):
                if nf not in face_tag:
                    face_tag[nf] = True
                    q.append(nf)
                    island.append(nf)
        islands.append(island)

    if len(islands) <= 1:
        bm.free()
        return False

    islands.sort(key=lambda isl: len(isl), reverse=True)
    remove_faces = [f for isl in islands[1:] for f in isl]

    bmesh.ops.delete(bm, geom=remove_faces, context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    bmesh.ops.delete(bm, geom=[e for e in bm.edges if not e.link_faces], context='EDGES')

    bm.to_mesh(me)
    bm.free()
    me.update()
    return True


# ============================================================
# Blob (metaball) + displace + floor flatten
# ============================================================

def _estimate_clearance(point: Vector, bvh: BVHTree) -> float:
    dirs = [
        Vector((1, 0, 0)), Vector((-1, 0, 0)),
        Vector((0, 1, 0)), Vector((0, -1, 0)),
        Vector((0, 0, 1)), Vector((0, 0, -1)),
    ]
    best = 1.0e9
    eps = 1e-5
    for d in dirs:
        loc, normal, index, dist = bvh.ray_cast(point + d * eps, d, 1.0e9)
        if loc is not None and dist is not None:
            best = min(best, dist)
    return best


def _create_blob_metaball(
    name: str,
    center: Vector,
    base_radius: float,
    height_scale: float,
    outline_chaos_01: float,
    rng: random.Random,
    resolution: float
) -> bpy.types.Object:
    chaos = max(0.0, min(1.0, outline_chaos_01))

    mb = bpy.data.metaballs.new(name + "_MB")
    mb.resolution = resolution
    mb.render_resolution = resolution

    obj = bpy.data.objects.new(name, mb)
    bpy.context.collection.objects.link(obj)
    obj.location = center

    # yaw only (floor stays flat because we flatten after conversion)
    obj.rotation_euler = (0.0, 0.0, rng.uniform(0.0, math.tau))

    core = mb.elements.new(type='BALL')
    core.radius = base_radius * rng.uniform(0.92, 1.10)
    core.stiffness = 2.0

    obj.scale.z *= max(0.05, height_scale)

    satellite_min = 6
    satellite_max = 14 + int(18 * chaos)
    satellite_count = rng.randint(satellite_min, max(satellite_min, satellite_max))

    shell = base_radius * (0.55 + 0.95 * chaos)
    z_shell = base_radius * (0.35 + 0.70 * chaos)
    capsule_chance = 0.02 + 0.06 * chaos

    for _ in range(satellite_count):
        use_capsule = (rng.random() < capsule_chance) and (chaos > 0.55)
        if use_capsule:
            e = mb.elements.new(type='CAPSULE')
            e.radius = base_radius * rng.uniform(0.18, 0.40)
            e.stiffness = 2.0
            e.size_x = base_radius * rng.uniform(0.20, 0.75)
        else:
            e = mb.elements.new(type='BALL')
            e.radius = base_radius * rng.uniform(0.12, 0.55) * (0.95 + 0.35 * chaos)
            e.stiffness = 2.0

        off = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)))
        if off.length > 1e-9:
            off.normalize()
        off.z *= (0.55 + 0.45 * chaos)

        r = rng.uniform(0.15, 1.0) ** (0.75 + 0.75 * (1.0 - chaos))
        e.co = Vector((off.x * r * shell, off.y * r * shell, off.z * r * z_shell))

    return obj


def _convert_to_mesh(obj: bpy.types.Object) -> bpy.types.Object:
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.convert(target='MESH')
    return bpy.context.view_layer.objects.active


def _join_meshes(target: bpy.types.Object, others: list[bpy.types.Object]) -> None:
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    for o in others:
        if o and o.name in bpy.data.objects:
            o.select_set(True)
    bpy.ops.object.join()


def _flatten_blob_bottom(mesh_obj: bpy.types.Object, center_world: Vector, blob_radius: float, strength_01: float):
    strength = max(0.0, min(1.0, strength_01))
    if strength <= 0.0 or blob_radius <= 0.0:
        return
    plane_z = center_world.z - blob_radius * 0.35
    falloff = blob_radius * 0.55
    if falloff <= 1e-9:
        return

    me = mesh_obj.data
    bm = bmesh.new()
    bm.from_mesh(me)

    inv = mesh_obj.matrix_world.inverted()
    mw = mesh_obj.matrix_world

    for v in bm.verts:
        wp = mw @ v.co
        dz = wp.z - plane_z
        if dz < 0.0:
            t = min(1.0, (-dz) / falloff)
            ease = t * t * (3.0 - 2.0 * t)
            wp.z = wp.z + (-dz) * strength * ease
            v.co = inv @ wp

    bm.to_mesh(me)
    bm.free()
    me.update()


def _apply_blob_displace(mesh_obj: bpy.types.Object, chaos_01: float, blob_radius: float, rng: random.Random):
    chaos = max(0.0, min(1.0, chaos_01))
    if chaos <= 0.01 or blob_radius <= 1e-9:
        return

    strength = blob_radius * (0.035 + 0.10 * chaos)
    tex_scale = max(0.001, blob_radius * (0.55 - 0.25 * chaos))

    tex = bpy.data.textures.new(name=f"DC_Musgrave_{mesh_obj.name}_{rng.randint(0, 999999):06d}", type='MUSGRAVE')
    tex.musgrave_type = 'RIDGED_MULTIFRACTAL'
    tex.noise_scale = tex_scale
    tex.nabla = 0.03
    tex.intensity = 1.0
    tex.contrast = 1.2

    mod = mesh_obj.modifiers.new("DC_BlobDisplace", type='DISPLACE')
    mod.texture = tex
    mod.strength = strength
    mod.mid_level = 0.0
    mod.texture_coords = 'LOCAL'

    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except RuntimeError:
        pass


# ============================================================
# Shops: beveled cuboid room (flat floor, random yaw, height fixed)
# ============================================================

def _raycast_floor_z(bvh: BVHTree | None, origin: Vector, max_dist: float) -> float | None:
    if bvh is None:
        return None
    direction = Vector((0.0, 0.0, -1.0))
    hit, nrm, face_i, dist = bvh.ray_cast(origin, direction, max_dist)
    if hit is None:
        return None
    return hit.z


def _sample_floor_z_multi(
    tunnel_bvh: BVHTree | None,
    volume_bvh: BVHTree | None,
    node_pos: Vector,
    room_half_diag: float,
    tunnel_radius: float
) -> float:
    """
    Cast multiple rays to avoid hitting ceilings. Take the LOWEST hit among samples.
    Prefer tunnel BVH, fallback to volume BVH.
    """
    up = max(tunnel_radius * 6.0, room_half_diag * 2.0, 0.1)
    down = max(tunnel_radius * 40.0, room_half_diag * 10.0, 1.0)

    offsets = [
        Vector((0.0, 0.0, 0.0)),
        Vector((room_half_diag * 0.15, 0.0, 0.0)),
        Vector((-room_half_diag * 0.15, 0.0, 0.0)),
        Vector((0.0, room_half_diag * 0.15, 0.0)),
        Vector((0.0, -room_half_diag * 0.15, 0.0)),
    ]

    hits = []

    for off in offsets:
        o = node_pos + off + Vector((0.0, 0.0, up))
        z = _raycast_floor_z(tunnel_bvh, o, down)
        if z is None:
            z = _raycast_floor_z(volume_bvh, o, down)
        if z is not None:
            hits.append(z)

    if hits:
        floor_z = min(hits)
    else:
        floor_z = node_pos.z

    # tiny lift to avoid z-fighting
    floor_z += max(0.0, tunnel_radius * 0.05)
    return floor_z


def _create_shop_cuboid_room(
    name: str,
    node_pos: Vector,
    tunnel_radius: float,
    rng: random.Random,
    tunnel_bvh: BVHTree | None,
    volume_bvh: BVHTree | None,
) -> tuple[bpy.types.Object, Vector]:
    """
    Shop room:
      - Width/Depth: (4x tunnel width) * rand(1..2)
      - Height: 1.5x tunnel width (fixed)
      - Random yaw rotation on Z
      - Bottom sits on "floor plane" found via multi-ray
    Returns (room_obj, marker_pos_world_at_room_center).
    """
    tunnel_width = max(1e-9, tunnel_radius * 2.0)

    base_w = 4.0 * tunnel_width
    base_d = 4.0 * tunnel_width
    h = 1.5 * tunnel_width  # HARD height

    w_scale = rng.uniform(1.0, 2.0)
    d_scale = rng.uniform(1.0, 2.0)

    w = base_w * w_scale
    d = base_d * d_scale

    # estimate diag for floor sampling offsets
    half_diag = math.sqrt((w * 0.5) ** 2 + (d * 0.5) ** 2)

    floor_z = _sample_floor_z_multi(
        tunnel_bvh=tunnel_bvh,
        volume_bvh=volume_bvh,
        node_pos=node_pos,
        room_half_diag=half_diag,
        tunnel_radius=tunnel_radius
    )

    # Place cube center so bottom sits on floor
    loc = Vector((node_pos.x, node_pos.y, floor_z + (h * 0.5)))

    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')

    bpy.ops.mesh.primitive_cube_add(size=1.0, enter_editmode=False, location=loc)
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    obj.data.name = name + "_Mesh"

    # Apply randomized yaw (horizontal plane rotation)
    obj.rotation_euler = (0.0, 0.0, rng.uniform(0.0, math.tau))

    # Scale after rotation is fine (still axis-aligned in object space)
    obj.scale = (w * 0.5, d * 0.5, h * 0.5)

    bev = obj.modifiers.new("DC_ShopBevel", type='BEVEL')
    bev.affect = 'EDGES'
    bev.offset_type = 'PERCENT'
    bev.width = 8.0
    bev.segments = 1
    bev.profile = 0.5

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.modifier_apply(modifier=bev.name)
    except RuntimeError:
        pass

    try:
        bpy.ops.object.shade_smooth()
    except RuntimeError:
        pass

    # Marker goes to the cuboid volumetric center (after scaling/rotation)
    marker_center = obj.matrix_world @ Vector((0.0, 0.0, 0.0))
    return obj, marker_center


# ============================================================
# Distribution selection
# ============================================================

def _floor_percent(total: int, percent_int: int) -> int:
    if total <= 0:
        return 0
    return int((total * max(0, percent_int)) // 100)


def _blue_noise_select(
    candidates: list[int],
    points: list[Vector],
    k: int,
    spread_01: float,
    base_spacing: float,
    rng: random.Random,
) -> list[int]:
    k = max(0, min(k, len(candidates)))
    if k == 0:
        return []
    if len(candidates) == k:
        return candidates[:]

    spread = max(0.0, min(1.0, spread_01))
    if spread <= 1e-6:
        pool = candidates[:]
        rng.shuffle(pool)
        return pool[:k]

    min_sep = max(0.0, base_spacing * (0.0 + 4.0 * spread))
    hash3 = SpatialHash3D(bucket_size=max(1e-6, min_sep))
    chosen: list[int] = []

    pool = candidates[:]
    rng.shuffle(pool)

    def can_accept(idx: int) -> bool:
        p = points[idx]
        near = hash3.query_bucket_neighbors(p, min_sep)
        if not near:
            return True
        ms2 = min_sep * min_sep
        for j in near:
            if (points[j] - p).length_squared < ms2:
                return False
        return True

    for idx in pool:
        if len(chosen) >= k:
            break
        if can_accept(idx):
            chosen.append(idx)
            hash3.insert(idx, points[idx])

    if len(chosen) < k:
        min_sep2 = max(0.0, min_sep * 0.5)
        if min_sep2 > 1e-9:
            hash3 = SpatialHash3D(bucket_size=max(1e-6, min_sep2))
            for idx in chosen:
                hash3.insert(idx, points[idx])

            def can_accept2(idx: int) -> bool:
                p = points[idx]
                near = hash3.query_bucket_neighbors(p, min_sep2)
                if not near:
                    return True
                ms2 = min_sep2 * min_sep2
                for j in near:
                    if (points[j] - p).length_squared < ms2:
                        return False
                return True

            for idx in pool:
                if len(chosen) >= k:
                    break
                if idx in chosen:
                    continue
                if can_accept2(idx):
                    chosen.append(idx)
                    hash3.insert(idx, points[idx])

    if len(chosen) < k:
        remaining = [i for i in pool if i not in set(chosen)]
        chosen.extend(remaining[: (k - len(chosen))])

    return chosen[:k]


# ============================================================
# Node markers
# ============================================================

def _get_or_make_collection(name: str) -> bpy.types.Collection:
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def _make_node_marker(collection: bpy.types.Collection, name: str, loc: Vector, node_type: str, node_index: int) -> bpy.types.Object:
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = 'SPHERE'
    empty.empty_display_size = 0.0015
    empty.location = loc
    empty["dc_node_type"] = node_type
    empty["dc_node_index"] = int(node_index)
    collection.objects.link(empty)
    return empty


def _create_named_node_markers(
    nodes: list[Vector],
    allowed_indices: set[int],
    final_mesh: bpy.types.Object | None,
    player_loc_world: Vector,
    nemesis_idx: int | None,
    chamber_nodes: list[int],
    trap_nodes: list[int],
    enemy_nodes: list[int],
    # shops become authoritative by rooms, so we pass explicit marker positions:
    shop_marker_positions: list[tuple[int, Vector]],
):
    col = _get_or_make_collection("DungeonColonizer_Nodes")
    for o in [o for o in list(col.objects) if o.name.startswith("node_")]:
        bpy.data.objects.remove(o, do_unlink=True)

    # Always: player marker at origin
    _make_node_marker(col, f"{NODE_TYPES['playerSpawn']}[01]", player_loc_world, "playerSpawn", 0)

    final_bvh = _build_bvh_from_mesh_object_direct(final_mesh) if final_mesh else None

    def marker_pos_for_idx(i: int) -> Vector:
        p = nodes[i].copy()
        if final_bvh is not None and not _point_inside_mesh_bvh(p, final_bvh):
            loc, nrm = _nearest_point_on_mesh_world(final_mesh, p)
            if loc is not None:
                if nrm is not None and nrm.length > 1e-9:
                    nrm = nrm.normalized()
                    p = loc - nrm * 0.002
                else:
                    p = loc
        return p

    # Nemesis
    if nemesis_idx is not None:
        _make_node_marker(col, f"{NODE_TYPES['nemesisSpawn']}[01]", marker_pos_for_idx(nemesis_idx), "nemesisSpawn", nemesis_idx)

    # Chambers
    c_count = 0
    for ci in sorted(set(chamber_nodes)):
        if ci not in allowed_indices:
            continue
        c_count += 1
        _make_node_marker(col, f"{NODE_TYPES['chamber']}[{c_count:02d}]", marker_pos_for_idx(ci), "chamber", ci)

    # Shops: ALWAYS create one marker per room (authoritative), even if node idx gets culled
    s_count = 0
    for (node_idx, pos) in shop_marker_positions:
        s_count += 1
        # store node_idx if it came from skeleton, else -1
        _make_node_marker(col, f"{NODE_TYPES['shop']}[{s_count:02d}]", pos, "shop", node_idx)

    # Traps
    t_count = 0
    for ti in sorted(set(trap_nodes)):
        if ti not in allowed_indices:
            continue
        t_count += 1
        _make_node_marker(col, f"{NODE_TYPES['trap']}[{t_count:05d}]", marker_pos_for_idx(ti), "trap", ti)

    # Enemies
    e_count = 0
    for ei in sorted(set(enemy_nodes)):
        if ei not in allowed_indices:
            continue
        e_count += 1
        _make_node_marker(col, f"{NODE_TYPES['enemySpawn']}[{e_count:05d}]", marker_pos_for_idx(ei), "enemySpawn", ei)

    # Mundane nodes: exclude specials we actually placed
    special = set(chamber_nodes) | set(trap_nodes) | set(enemy_nodes)
    if nemesis_idx is not None:
        special.add(nemesis_idx)

    # Exclude shop node indices (if any)
    for (node_idx, _pos) in shop_marker_positions:
        if node_idx >= 0:
            special.add(node_idx)

    m_count = 0
    for idx in sorted(allowed_indices):
        if idx in special:
            continue
        m_count += 1
        _make_node_marker(col, f"{NODE_TYPES['mundane']}[{m_count:06d}]", marker_pos_for_idx(idx), "mundane", idx)

    return col


# ============================================================
# Degree limiting (max 4, rare)
# ============================================================

def _degree_accept_prob(deg: int) -> float:
    if deg <= 1:
        return 1.0
    if deg == 2:
        return 0.55
    if deg == 3:
        return 0.10
    return 0.0


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
# Space Colonization
# ============================================================

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _map_slider_01_to_01_soft(x: float) -> float:
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

    vf = _clamp01(vertical_freq_01)
    z_scale = 0.15 + 0.85 * (0.05 + 0.95 * vf)

    step_size = max(1e-9, step_size)
    influence_radius = max(step_size * 1.01, influence_radius)
    kill_radius = max(1e-9, kill_radius)

    attract_hash = SpatialHash3D(bucket_size=influence_radius)
    attract_hash.rebuild(list(range(len(attractors))), attractors)
    alive = [True] * len(attractors)

    nodes: list[Vector] = [Vector((0.0, 0.0, 0.0))]
    lineage: list[int] = [0]
    next_lineage_id = 1

    degrees: list[int] = [0]
    edges_set: set[tuple[int, int]] = set()
    edges: list[tuple[int, int]] = []

    node_hash = SpatialHash3D(bucket_size=influence_radius)
    node_hash.insert(0, nodes[0])

    trunk_idx = 0
    found = False
    for _ in range(20000):
        p = nodes[trunk_idx]
        for ai in attract_hash.query_bucket_neighbors(p, influence_radius):
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
                for ni in node_hash.query_bucket_neighbors(a, search_r):
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

        merge_thresh2 = (step_size * 0.25) ** 2
        new_to_final = []
        for idx_local, p in enumerate(new_nodes):
            merged = -1
            for ni in node_hash.query_bucket_neighbors(p, step_size * 0.5):
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

        for (parent_i, _tmp), child_i in zip(new_edges, new_to_final):
            _try_add_edge(edges_set, edges, degrees, parent_i, child_i, rng)

        connect_dist = step_size * 1.5
        connect_dist2 = connect_dist * connect_dist
        touched = set(new_to_final)

        for i in touched:
            if degrees[i] >= 4:
                continue
            if rng.random() > conn:
                continue

            pi = nodes[i]
            cand = node_hash.query_bucket_neighbors(pi, connect_dist)
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
                for j in node_hash.query_bucket_neighbors(pi, end_dist):
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
# Mesh build helpers
# ============================================================

def _ensure_skin_and_set_radius_bmesh(obj: bpy.types.Object, radius: float):
    radius = max(0.0, radius)
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
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.modifier_apply(modifier=mod_name)
    except RuntimeError:
        pass


def _apply_smoothing(obj: bpy.types.Object, mode: str, factor: float = 0.5, iterations: int = 5):
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
    voxel_size = max(1e-6, voxel_size)
    m = obj.modifiers.new("DC_VoxelRemesh", type='REMESH')
    m.mode = 'VOXEL'
    m.voxel_size = voxel_size
    m.adaptivity = max(0.0, min(1.0, adaptivity))
    m.use_smooth_shade = True
    _apply_modifier(obj, m.name)


def _apply_quadriflow_remesh(obj: bpy.types.Object, target_faces: int):
    target_faces = max(100, int(target_faces))
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
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


def _make_skeleton_mesh(name: str, nodes: list[Vector], edges: list[tuple[int, int]]) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name + "_SkeletonMesh")
    obj = bpy.data.objects.new(name + "_Skeleton", mesh)
    bpy.context.collection.objects.link(obj)
    mesh.from_pydata([p.to_tuple() for p in nodes], edges, [])
    mesh.update()
    return obj


def _skeleton_to_curve(skel_obj: bpy.types.Object, curve_name: str) -> bpy.types.Object:
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
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
        description="Shareable generation ID string (seed + key slider values + chambers).",
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

    cleanup_skeleton_islands: BoolProperty(
        name="Clean Skeleton Islands",
        description="If enabled, keeps only the largest connected skeleton component before building outputs.",
        default=True
    )

    chamber_outline_chaos: FloatProperty(
        name="Chamber Outline Chaos",
        description="Makes chamber/bulge silhouettes more chaotic (also drives blob displace definition).",
        default=0.65,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )

    chamber_floor_flatness: FloatProperty(
        name="Chamber Floor Flatness",
        description="Flattens the bottom of chambers/bulges.",
        default=0.6,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )

    chamber_count: IntProperty(
        name="Chambers",
        description="Number of blobby chambers to add at random nodes (0–20).",
        default=2,
        min=0,
        max=20
    )

    spawn_bulges: BoolProperty(
        name="Spawn Bulges",
        description="Adds blob expansions at playerSpawn (origin) and nemesisSpawn.",
        default=True
    )

    spawn_bulge_scale: FloatProperty(
        name="Spawn Bulge Scale",
        description="Multiplier on tunnel radius for spawn bulge size.",
        default=3.0,
        min=1.0,
        max=8.0
    )

    special_distribution: FloatProperty(
        name="Special Distribution",
        description="0 = random placement. 1 = more evenly distributed spacing between shop/trap/enemy nodes.",
        default=0.65,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )

    add_shop_nodes: BoolProperty(
        name="Add Shop Nodes",
        description="Enable shop nodes. Shops get a beveled cuboid room (floored to tunnel surface).",
        default=False
    )

    shop_count: IntProperty(
        name="Shop Count",
        description="Number of shop nodes/rooms to place (1–15). GUARANTEED: the generator will always create exactly this many shop rooms and markers.",
        default=2,
        min=1,
        max=15
    )

    add_trap_nodes: BoolProperty(
        name="Add Trap Nodes",
        description="Enable trap nodes among mundane nodes. Traps do not alter geometry.",
        default=False
    )

    trap_percent: IntProperty(
        name="Trap Percent",
        description="Percent of mundane nodes converted into traps (1–20%). Rounded down.",
        default=5,
        min=1,
        max=20
    )

    add_enemy_spawn_nodes: BoolProperty(
        name="Add Enemy Spawn Nodes",
        description="Enable enemy spawn nodes among mundane nodes. These do not alter geometry.",
        default=False
    )

    enemy_percent_enum: EnumProperty(
        name="Enemy Spawn Percent",
        description="Percent of mundane nodes converted into enemy spawns. Rounded down.",
        items=[
            ('1', "1%", "1% (rounded down)"),
            ('5', "5%", "5% (rounded down)"),
            ('10', "10%", "10% (rounded down)"),
            ('15', "15%", "15% (rounded down)"),
            ('20', "20%", "20% (rounded down)"),
            ('25', "25%", "25% (rounded down)"),
            ('30', "30%", "30% (rounded down)"),
        ],
        default='10'
    )

    out_mesh: BoolProperty(
        name="Tunnel Mesh",
        description="Output the final 3D tunnel mesh.",
        default=True
    )

    out_curve: BoolProperty(
        name="Curve Skeleton",
        description="Output a curve representing the tunnel skeleton.",
        default=False
    )

    out_node_markers: BoolProperty(
        name="Node Markers",
        description="Output named node markers as empties.",
        default=True
    )

    tunnel_radius: FloatProperty(
        name="Tunnel Radius",
        description="Tube radius used by the Skin modifier.",
        default=0.02,
        min=0.0,
        max=10.0,
        subtype='DISTANCE',
        unit='LENGTH',
    )

    apply_manifold_remesh: BoolProperty(
        name="Make Manifold",
        description="If enabled, remesh the skinned tubes into a cohesive mesh.",
        default=True
    )

    remesh_mode: EnumProperty(
        name="Remesh Definition",
        description="Choose how to remesh.",
        items=[
            ('NONE', "None", "No remesh."),
            ('VOXEL', "Voxel Size", "Voxel remesh (best for fusing blobs/shops)."),
            ('FACE_COUNT', "Target Faces", "QuadriFlow remesh (slow)."),
        ],
        default='VOXEL'
    )

    auto_voxel: BoolProperty(
        name="Auto Voxel Size",
        description="Compute voxel size from tunnel radius.",
        default=True
    )

    voxel_mult: FloatProperty(
        name="Voxel Mult",
        description="Auto voxel multiplier: voxel_size = tunnel_radius * voxel_mult.",
        default=0.6,
        min=0.1,
        max=2.0
    )

    voxel_size: FloatProperty(
        name="Voxel Size",
        description="Manual voxel size for Voxel Remesh (only when Auto Voxel Size is OFF).",
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
        description="Optional post-smoothing applied after remesh.",
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

    cleanup_loose_parts: BoolProperty(
        name="Remove Loose Parts",
        description="If enabled, deletes disconnected mesh islands after remeshing by keeping only the largest island.",
        default=True
    )


def _update_gen_id(props: DC_Properties, seed_used: int):
    v = f"{props.vertical_frequency:.2f}"
    c = f"{props.connection_bias:.2f}"
    i = f"{props.isolation_bias:.2f}"
    ch = int(props.chamber_count)
    props.gen_id = f"GenID: {seed_used}-{v}-{c}-{i}-ch{ch}"


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
    bl_description = "Run space colonization inside the selected volume and output tunnels/chambers/shops/nodes/curve as enabled."
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.dc_props

        if not (props.out_mesh or props.out_curve or props.out_node_markers):
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

        if props.cleanup_skeleton_islands:
            nodes, edges = _prune_skeleton_keep_largest_component(nodes=nodes, edges=edges)

        base_name = "DungeonColonizer"
        created = []

        skel_obj = _make_skeleton_mesh(base_name, nodes, edges)

        if props.out_curve:
            curve_obj = _skeleton_to_curve(skel_obj, base_name + "_Curve")
            created.append(curve_obj)

        vol_bvh = _build_bvh_from_object(props.target_obj)
        player_loc = Vector((0.0, 0.0, 0.0))
        final_mesh_obj = None

        def compute_voxel_size():
            return max(1e-6, props.tunnel_radius * props.voxel_mult) if props.auto_voxel else max(1e-6, props.voxel_size)

        # Skeleton -> Skin -> Apply
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        skel_obj.select_set(True)
        bpy.context.view_layer.objects.active = skel_obj
        bpy.ops.object.duplicate()
        tube_src = bpy.context.view_layer.objects.active
        tube_src.name = base_name + "_TubeSrc"
        _ensure_skin_and_set_radius_bmesh(tube_src, props.tunnel_radius)
        _apply_modifier(tube_src, "DC_Skin")

        # Tunnel BVH for shop floors
        tunnel_bvh = _build_bvh_from_mesh_object_direct(tube_src)

        # Nodes inside original volume (pre)
        allowed_pre = {i for i, p in enumerate(nodes) if _point_inside_mesh_bvh(p, vol_bvh)}

        # Nemesis at lowest z among allowed_pre
        nemesis_idx = None
        if allowed_pre:
            min_z = min(nodes[i].z for i in allowed_pre)
            eps = 1e-6
            lowest = [i for i in allowed_pre if abs(nodes[i].z - min_z) <= eps]
            nemesis_idx = rng.choice(lowest) if lowest else rng.choice(list(allowed_pre))

        chamber_nodes: list[int] = []
        trap_nodes: list[int] = []
        enemy_nodes: list[int] = []

        reserved = set()
        if nemesis_idx is not None:
            reserved.add(nemesis_idx)

        # Chambers selection
        if props.chamber_count > 0 and allowed_pre:
            candidates = [i for i in allowed_pre if i not in reserved]
            count = min(int(props.chamber_count), len(candidates))
            rng.shuffle(candidates)
            chamber_nodes = candidates[:count]
            reserved |= set(chamber_nodes)

        # For traps/enemies, pick from mundane inside volume
        mundane_pool = [i for i in allowed_pre if i not in reserved]

        if props.add_trap_nodes and mundane_pool:
            k_trap = _floor_percent(len(mundane_pool), int(props.trap_percent))
            if k_trap > 0:
                trap_nodes = _blue_noise_select(
                    candidates=mundane_pool,
                    points=nodes,
                    k=k_trap,
                    spread_01=props.special_distribution,
                    base_spacing=props.node_spacing,
                    rng=rng,
                )
                reserved |= set(trap_nodes)
                mundane_pool = [i for i in mundane_pool if i not in reserved]

        if props.add_enemy_spawn_nodes and mundane_pool:
            pct = int(props.enemy_percent_enum)
            k_enemy = _floor_percent(len(mundane_pool), pct)
            if k_enemy > 0:
                enemy_nodes = _blue_noise_select(
                    candidates=mundane_pool,
                    points=nodes,
                    k=k_enemy,
                    spread_01=props.special_distribution,
                    base_spacing=props.node_spacing,
                    rng=rng,
                )
                reserved |= set(enemy_nodes)
                mundane_pool = [i for i in mundane_pool if i not in reserved]

        # --- SHOPS: GUARANTEE COUNT BY ROOMS ---
        # We still choose anchors from mundane_pool (so they’re meaningfully inside), but even if later culling occurs,
        # markers will be placed at the *room center* and counted 1:1 with rooms.
        desired_shops = int(props.shop_count) if props.add_shop_nodes else 0
        shop_anchor_indices: list[int] = []
        if desired_shops > 0:
            if mundane_pool:
                k = min(desired_shops, len(mundane_pool))
                shop_anchor_indices = _blue_noise_select(
                    candidates=mundane_pool,
                    points=nodes,
                    k=k,
                    spread_01=props.special_distribution,
                    base_spacing=props.node_spacing,
                    rng=rng,
                )
            # If we have fewer anchors than desired, we will still create rooms by reusing anchors,
            # but we do it by *sampling nearby indices* to keep them distributed.
            if len(shop_anchor_indices) < desired_shops:
                # fallback pool = all allowed_pre mundane-ish indices (including ones used by traps/enemies)
                fallback = [i for i in allowed_pre if i not in reserved]
                if not fallback:
                    fallback = list(allowed_pre) if allowed_pre else [0]
                rng.shuffle(fallback)
                while len(shop_anchor_indices) < desired_shops:
                    shop_anchor_indices.append(fallback[len(shop_anchor_indices) % len(fallback)])

        # Geometry build
        shop_marker_positions: list[tuple[int, Vector]] = []  # (node_idx, center_pos) — authoritative

        if props.out_mesh:
            blob_meshes = []
            mb_res = max(0.05, min(0.35, props.tunnel_radius * 0.8))
            vsize = compute_voxel_size()

            # Spawn bulges
            if props.spawn_bulges:
                bulge_r = max(1e-9, props.tunnel_radius * float(props.spawn_bulge_scale))

                origin_inside = _point_inside_mesh_bvh(player_loc, vol_bvh)
                player_radius = bulge_r
                if origin_inside:
                    clearance = _estimate_clearance(player_loc, vol_bvh)
                    if clearance > 1e-6:
                        player_radius = min(player_radius, clearance * 0.85)

                mb = _create_blob_metaball(
                    name=f"{base_name}_PlayerSpawn_Bulge",
                    center=player_loc,
                    base_radius=player_radius,
                    height_scale=rng.uniform(0.85, 1.25),
                    outline_chaos_01=props.chamber_outline_chaos,
                    rng=rng,
                    resolution=mb_res,
                )
                m = _convert_to_mesh(mb)
                _apply_blob_displace(m, props.chamber_outline_chaos, player_radius, rng)
                _flatten_blob_bottom(m, player_loc, player_radius, props.chamber_floor_flatness)
                blob_meshes.append(m)

                if nemesis_idx is not None:
                    center = nodes[nemesis_idx]
                    clearance = _estimate_clearance(center, vol_bvh)
                    if clearance > 1e-6:
                        radius = min(bulge_r, clearance * 0.85)
                        mb = _create_blob_metaball(
                            name=f"{base_name}_NemesisSpawn_Bulge",
                            center=center,
                            base_radius=radius,
                            height_scale=rng.uniform(0.85, 1.25),
                            outline_chaos_01=props.chamber_outline_chaos,
                            rng=rng,
                            resolution=mb_res,
                        )
                        m = _convert_to_mesh(mb)
                        _apply_blob_displace(m, props.chamber_outline_chaos, radius, rng)
                        _flatten_blob_bottom(m, center, radius, props.chamber_floor_flatness)
                        blob_meshes.append(m)

            # Chambers (max diameter multiplier = 10.0)
            if props.chamber_count > 0 and chamber_nodes:
                diameter = max(1e-9, props.tunnel_radius * 2.0)
                for idx, ni in enumerate(chamber_nodes):
                    center = nodes[ni]
                    width = diameter * rng.uniform(2.0, 10.0)
                    radius = width * 0.5
                    height_scale = rng.uniform(0.5, 2.0)

                    clearance = _estimate_clearance(center, vol_bvh)
                    if clearance < 1e-6:
                        continue
                    max_r = clearance * 0.85
                    radius = min(radius, max_r)

                    height_radius = radius * height_scale
                    if height_radius > max_r:
                        height_scale = max_r / max(1e-9, radius)

                    mb = _create_blob_metaball(
                        name=f"{base_name}_Chamber_{idx:02d}",
                        center=center,
                        base_radius=radius,
                        height_scale=height_scale,
                        outline_chaos_01=props.chamber_outline_chaos,
                        rng=rng,
                        resolution=mb_res,
                    )
                    chamber_mesh = _convert_to_mesh(mb)
                    _apply_blob_displace(chamber_mesh, props.chamber_outline_chaos, radius, rng)
                    _flatten_blob_bottom(chamber_mesh, center, radius, props.chamber_floor_flatness)
                    blob_meshes.append(chamber_mesh)

            # Shops: create EXACTLY desired_shops rooms, place markers at cuboid centers
            if props.add_shop_nodes and desired_shops > 0:
                for s_idx in range(desired_shops):
                    ni = shop_anchor_indices[s_idx] if shop_anchor_indices else 0
                    center = nodes[ni] if 0 <= ni < len(nodes) else Vector((0, 0, 0))
                    shop_obj, center_pos = _create_shop_cuboid_room(
                        name=f"{base_name}_ShopRoom_{s_idx:02d}",
                        node_pos=center,
                        tunnel_radius=props.tunnel_radius,
                        rng=rng,
                        tunnel_bvh=tunnel_bvh,
                        volume_bvh=vol_bvh,
                    )
                    blob_meshes.append(shop_obj)
                    shop_marker_positions.append((int(ni), center_pos))

            if blob_meshes:
                _join_meshes(tube_src, blob_meshes)

            remeshed = False
            if props.apply_manifold_remesh and props.remesh_mode == 'VOXEL':
                _apply_voxel_remesh(tube_src, vsize, adaptivity=0.0)
                remeshed = True
            elif props.apply_manifold_remesh and props.remesh_mode == 'FACE_COUNT':
                _apply_quadriflow_remesh(tube_src, props.target_faces)
                remeshed = True

            if props.cleanup_loose_parts and remeshed:
                _remove_loose_parts_keep_largest(tube_src)

            _apply_smoothing(
                tube_src,
                props.smoothing_mode,
                factor=props.smoothing_factor,
                iterations=props.smoothing_iterations
            )

            tube_src.name = base_name + "_TunnelMesh"
            created.append(tube_src)
            final_mesh_obj = tube_src

            bpy.data.objects.remove(skel_obj, do_unlink=True)

        else:
            if not props.out_curve:
                skel_obj.name = base_name + "_Skeleton"
                created.append(skel_obj)
            else:
                bpy.data.objects.remove(skel_obj, do_unlink=True)

        # Node markers: compute allowed against FINAL mesh if possible
        if props.out_node_markers:
            if final_mesh_obj is not None and final_mesh_obj.type == 'MESH' and len(final_mesh_obj.data.polygons) > 0:
                final_bvh = _build_bvh_from_mesh_object_direct(final_mesh_obj)
                allowed = {i for i, p in enumerate(nodes) if final_bvh and _point_inside_mesh_bvh(p, final_bvh)}
            else:
                allowed = {i for i, p in enumerate(nodes) if _point_inside_mesh_bvh(p, vol_bvh)}

            _create_named_node_markers(
                nodes=nodes,
                allowed_indices=allowed,
                final_mesh=final_mesh_obj,
                player_loc_world=player_loc,
                nemesis_idx=nemesis_idx,
                chamber_nodes=chamber_nodes,
                trap_nodes=trap_nodes,
                enemy_nodes=enemy_nodes,
                shop_marker_positions=shop_marker_positions,  # authoritative
            )

        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        for o in created:
            if o and o.name in bpy.data.objects:
                o.select_set(True)
        if created:
            bpy.context.view_layer.objects.active = created[-1]

        self.report(
            {'INFO'},
            f"{props.gen_id} | Nodes:{len(nodes)} Edges:{len(edges)} Chambers:{len(chamber_nodes)} Shops:{len(shop_marker_positions)}"
        )
        return {'FINISHED'}


# ============================================================
# UI Panel
# ============================================================

class DC_PT_panel(bpy.types.Panel):
    bl_label = "Dungeon Colonizer"
    bl_idname = "DC_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Dungeon Colonizer"

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
        col.prop(props, "cleanup_skeleton_islands")

        col.separator()
        col.label(text="Blob Shape (Shared)")
        col.prop(props, "chamber_outline_chaos")
        col.prop(props, "chamber_floor_flatness")

        col.separator()
        col.label(text="Chambers")
        col.prop(props, "chamber_count")

        col.separator()
        col.label(text="Spawn Areas")
        col.prop(props, "spawn_bulges")
        if props.spawn_bulges:
            col.prop(props, "spawn_bulge_scale")

        col.separator()
        col.label(text="Special Nodes")
        col.prop(props, "special_distribution")

        col.prop(props, "add_shop_nodes")
        if props.add_shop_nodes:
            col.prop(props, "shop_count")

        col.prop(props, "add_trap_nodes")
        if props.add_trap_nodes:
            col.prop(props, "trap_percent")

        col.prop(props, "add_enemy_spawn_nodes")
        if props.add_enemy_spawn_nodes:
            col.prop(props, "enemy_percent_enum")

        col.separator()
        col.label(text="Outputs")
        row1 = col.row(align=True)
        row1.prop(props, "out_mesh")
        row1.prop(props, "out_curve")
        col.prop(props, "out_node_markers")

        col.separator()
        col.label(text="Tunnel Volume")
        col.prop(props, "tunnel_radius")
        col.prop(props, "apply_manifold_remesh")

        cleanup_row = col.row()
        cleanup_row.enabled = (props.apply_manifold_remesh and props.remesh_mode != 'NONE')
        cleanup_row.prop(props, "cleanup_loose_parts")

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
