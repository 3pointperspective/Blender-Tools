bl_info = {
    "name": "Maze-Maker Tool",
    "author": "Aidan Ault",
    "version": (1, 1),
    "blender": (4, 1, 0),
    "location": "View3D > Sidebar > Maze-Maker",
    "description": "Generate cuboid or mesh-filled 3D mazes with seed control and toggleable output style.",
    "category": "Add Mesh",
}

import bpy
import bmesh
import random
import mathutils
from bpy.props import IntProperty, BoolProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Vector
from mathutils.bvhtree import BVHTree

CUBE_SIZE = 0.1

# ------------------------- Shared Properties -------------------------

class MazeMakerSettings(PropertyGroup):
    use_unified_form: BoolProperty(
        name="Unified Form",
        default=True,
        description="Generate a single unified mesh or separate cubes"
    )

# ------------------------- Cuboid Maze Properties -------------------------

class CuboidMazeProperties(PropertyGroup):
    maze_x: IntProperty(name="Width", default=5, min=1)
    maze_y: IntProperty(name="Depth", default=5, min=1)
    maze_z: IntProperty(name="Height", default=5, min=1)
    seed: StringProperty(name="Seed", description="Optional seed for maze generation", default="")

# ------------------------- Mesh Fill Maze Properties -------------------------

class MeshMazeProperties(PropertyGroup):
    target_object: PointerProperty(
        name="Target Mesh",
        type=bpy.types.Object,
        description="Select the mesh object to fill with maze",
    )
    resolution: IntProperty(
        name="Resolution (paths per shortest axis)",
        default=10,
        min=3,
        description="Number of maze paths along shortest axis"
    )
    seed: StringProperty(name="Seed", description="Optional seed for mesh-fill maze", default="")

# ------------------------- Maze Generation Functions -------------------------

def generate_maze_array(width, depth, height):
    grid_x = width * 2 - 1
    grid_y = depth * 2 - 1
    grid_z = height * 2 - 1

    maze = [[[0 for _ in range(grid_z)] for _ in range(grid_y)] for _ in range(grid_x)]

    visited = set()
    stack = []

    start = (random.randrange(0, grid_x, 2), random.randrange(0, grid_y, 2), random.randrange(0, grid_z, 2))
    stack.append(start)
    visited.add(start)
    maze[start[0]][start[1]][start[2]] = 1

    directions = [(2, 0, 0), (-2, 0, 0), (0, 2, 0), (0, -2, 0), (0, 0, 2), (0, 0, -2)]

    while stack:
        cx, cy, cz = stack[-1]
        neighbors = []
        for dx, dy, dz in directions:
            nx, ny, nz = cx + dx, cy + dy, cz + dz
            if 0 <= nx < grid_x and 0 <= ny < grid_y and 0 <= nz < grid_z and (nx, ny, nz) not in visited:
                neighbors.append((nx, ny, nz))

        if neighbors:
            next_cell = random.choice(neighbors)
            nx, ny, nz = next_cell
            wall_x, wall_y, wall_z = (cx + nx) // 2, (cy + ny) // 2, (cz + nz) // 2
            maze[nx][ny][nz] = 1
            maze[wall_x][wall_y][wall_z] = 1
            visited.add(next_cell)
            stack.append(next_cell)
        else:
            stack.pop()

    return maze

def create_maze_mesh(maze):
    mesh = bpy.data.meshes.new("Maze")
    obj = bpy.data.objects.new("Maze", mesh)
    bpy.context.collection.objects.link(obj)

    verts = []
    faces = []
    offset = 0

    for x, layer in enumerate(maze):
        for y, row in enumerate(layer):
            for z, val in enumerate(row):
                if val == 1:
                    ox, oy, oz = x * CUBE_SIZE, y * CUBE_SIZE, z * CUBE_SIZE
                    cube_verts = [
                        (ox - CUBE_SIZE/2, oy - CUBE_SIZE/2, oz - CUBE_SIZE/2),
                        (ox + CUBE_SIZE/2, oy - CUBE_SIZE/2, oz - CUBE_SIZE/2),
                        (ox + CUBE_SIZE/2, oy + CUBE_SIZE/2, oz - CUBE_SIZE/2),
                        (ox - CUBE_SIZE/2, oy + CUBE_SIZE/2, oz - CUBE_SIZE/2),
                        (ox - CUBE_SIZE/2, oy - CUBE_SIZE/2, oz + CUBE_SIZE/2),
                        (ox + CUBE_SIZE/2, oy - CUBE_SIZE/2, oz + CUBE_SIZE/2),
                        (ox + CUBE_SIZE/2, oy + CUBE_SIZE/2, oz + CUBE_SIZE/2),
                        (ox - CUBE_SIZE/2, oy + CUBE_SIZE/2, oz + CUBE_SIZE/2),
                    ]
                    cube_faces = [
                        (0, 1, 2, 3), (4, 5, 6, 7),
                        (0, 1, 5, 4), (2, 3, 7, 6),
                        (1, 2, 6, 5), (0, 3, 7, 4),
                    ]
                    verts.extend(cube_verts)
                    faces.extend([(a+offset, b+offset, c+offset, d+offset) for a,b,c,d in cube_faces])
                    offset += 8

    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return obj

def create_unified_maze_mesh(maze):
    mesh = bpy.data.meshes.new("MazeUnified")
    obj = bpy.data.objects.new("MazeUnified", mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()

    half = CUBE_SIZE / 2.0
    face_dirs = {
        (1, 0, 0): [(half, -half, -half), (half, -half, half), (half, half, half), (half, half, -half)],
        (-1, 0, 0): [(-half, -half, -half), (-half, half, -half), (-half, half, half), (-half, -half, half)],
        (0, 1, 0): [(-half, half, -half), (half, half, -half), (half, half, half), (-half, half, half)],
        (0, -1, 0): [(-half, -half, -half), (-half, -half, half), (half, -half, half), (half, -half, -half)],
        (0, 0, 1): [(-half, -half, half), (-half, half, half), (half, half, half), (half, -half, half)],
        (0, 0, -1): [(-half, -half, -half), (half, -half, -half), (half, half, -half), (-half, half, -half)],
    }

    vert_cache = {}

    def get_vert_key(pos):
        return (round(pos.x, 5), round(pos.y, 5), round(pos.z, 5))

    def get_or_create_vert(pos):
        key = get_vert_key(pos)
        if key not in vert_cache:
            vert_cache[key] = bm.verts.new(pos)
        return vert_cache[key]

    sx, sy, sz = len(maze), len(maze[0]), len(maze[0][0])

    for x in range(sx):
        for y in range(sy):
            for z in range(sz):
                if maze[x][y][z] != 1:
                    continue
                cx, cy, cz = x * CUBE_SIZE, y * CUBE_SIZE, z * CUBE_SIZE
                center = mathutils.Vector((cx, cy, cz))
                for dx, dy, dz in face_dirs:
                    nx, ny, nz = x + dx, y + dy, z + dz
                    if not (0 <= nx < sx and 0 <= ny < sy and 0 <= nz < sz) or maze[nx][ny][nz] != 1:
                        face_verts = []
                        for offset in face_dirs[(dx, dy, dz)]:
                            world_pos = center + mathutils.Vector(offset)
                            face_verts.append(get_or_create_vert(world_pos))
                        try:
                            bm.faces.new(face_verts)
                        except ValueError:
                            pass

    bm.normal_update()
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.00001)
    bm.to_mesh(mesh)
    bm.free()
    return obj

def is_mesh_manifold(obj):
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    is_manifold = all(e.is_manifold for e in bm.edges)
    bm.free()
    return is_manifold

def point_inside_object(obj, point):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bvh = BVHTree.FromBMesh(bm)
    bm.free()

    count = 0
    direction = Vector((1, 0.123, 0.456))
    loc, normal, index, dist = bvh.ray_cast(point, direction)
    while index is not None:
        count += 1
        point = loc + direction * 0.0001
        loc, normal, index, dist = bvh.ray_cast(point, direction)
    eval_obj.to_mesh_clear()
    return count % 2 == 1

def generate_maze_cells(valid_cells):
    maze = set()
    visited = set()

    def cell_neighbors(cell):
        x, y, z = cell
        for dx, dy, dz in [(-2, 0, 0), (2, 0, 0), (0, -2, 0), (0, 2, 0), (0, 0, -2), (0, 0, 2)]:
            neighbor = (x + dx, y + dy, z + dz)
            if neighbor in valid_cells:
                yield neighbor, (dx // 2, dy // 2, dz // 2)

    start = random.choice(list(valid_cells))
    stack = [start]
    visited.add(start)
    maze.add(start)

    while stack:
        current = stack[-1]
        neighbors = [n for n, _ in cell_neighbors(current) if n not in visited]
        if neighbors:
            chosen = random.choice(neighbors)
            path = tuple((c + p) for c, p in zip(current, [(a - b) // 2 for a, b in zip(chosen, current)]))
            maze.add(chosen)
            maze.add(path)
            visited.add(chosen)
            stack.append(chosen)
        else:
            stack.pop()

    return maze

def build_maze_mesh(maze_cells, spacing, origin):
    mesh = bpy.data.meshes.new("MazeMesh")
    obj = bpy.data.objects.new("Maze", mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    for x, y, z in maze_cells:
        cube = bmesh.ops.create_cube(bm, size=spacing)
        bmesh.ops.translate(bm, verts=cube['verts'], vec=origin + Vector((x * spacing, y * spacing, z * spacing)))
    bm.to_mesh(mesh)
    bm.free()

def build_unified_maze_mesh(maze_cells, spacing, origin):
    mesh = bpy.data.meshes.new("MazeMeshUnified")
    obj = bpy.data.objects.new("MazeUnified", mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()

    half = spacing / 2.0
    face_dirs = {
        (1, 0, 0): [(half, -half, -half), (half, -half, half), (half, half, half), (half, half, -half)],
        (-1, 0, 0): [(-half, -half, -half), (-half, half, -half), (-half, half, half), (-half, -half, half)],
        (0, 1, 0): [(-half, half, -half), (half, half, -half), (half, half, half), (-half, half, half)],
        (0, -1, 0): [(-half, -half, -half), (-half, -half, half), (half, -half, half), (half, -half, -half)],
        (0, 0, 1): [(-half, -half, half), (-half, half, half), (half, half, half), (half, -half, half)],
        (0, 0, -1): [(-half, -half, -half), (half, -half, -half), (half, half, -half), (-half, half, -half)],
    }

    vert_cache = {}

    def get_vert_key(pos):
        return (round(pos.x, 5), round(pos.y, 5), round(pos.z, 5))

    def get_or_create_vert(pos):
        key = get_vert_key(pos)
        if key not in vert_cache:
            vert_cache[key] = bm.verts.new(pos)
        return vert_cache[key]

    for cell in maze_cells:
        cx, cy, cz = cell
        center = origin + Vector((cx * spacing, cy * spacing, cz * spacing))

        for dx, dy, dz in face_dirs.keys():
            neighbor = (cx + dx, cy + dy, cz + dz)
            if neighbor not in maze_cells:
                face_verts = []
                for offset in face_dirs[(dx, dy, dz)]:
                    world_pos = center + Vector(offset)
                    face_verts.append(get_or_create_vert(world_pos))
                try:
                    bm.faces.new(face_verts)
                except ValueError:
                    pass

    bm.normal_update()
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.00001)
    bm.to_mesh(mesh)
    bm.free()

# ------------------------- Operators -------------------------

class GenerateCuboidMazeOperator(bpy.types.Operator):
    bl_idname = "maze.generate_cuboid_maze"
    bl_label = "Cuboid Maze"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.maze_maker_settings
        props = context.scene.cuboid_maze_props

        seed_str = props.seed
        if seed_str.strip():
            seed = hash(seed_str)
        else:
            seed = random.randint(0, 999999)
            props.seed = str(seed)

        random.seed(seed)
        maze = generate_maze_array(props.maze_x, props.maze_y, props.maze_z)

        if settings.use_unified_form:
            create_unified_maze_mesh(maze)
        else:
            create_maze_mesh(maze)
        return {'FINISHED'}

class GenerateMeshFillMazeOperator(bpy.types.Operator):
    bl_idname = "maze.fill_mesh_maze"
    bl_label = "Fill Maze"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.maze_maker_settings
        props = context.scene.mesh_maze_props
        target_obj = props.target_object
        resolution = props.resolution

        if not target_obj or target_obj.type != 'MESH':
            self.report({'ERROR'}, "Please select a valid mesh object")
            return {'CANCELLED'}

        if not is_mesh_manifold(target_obj):
            self.report({'ERROR'}, "Object mesh is not manifold")
            return {'CANCELLED'}

        seed_str = props.seed
        if seed_str.strip():
            seed = hash(seed_str)
        else:
            seed = random.randint(0, 999999)
            props.seed = str(seed)

        random.seed(seed)

        bbox = [target_obj.matrix_world @ Vector(corner) for corner in target_obj.bound_box]
        min_bound = Vector((min(v[i] for v in bbox) for i in range(3)))
        max_bound = Vector((max(v[i] for v in bbox) for i in range(3)))
        dimensions = max_bound - min_bound

        spacing = min(dimensions) / (resolution * 2 + 1)
        grid_size = [int((d / spacing)) | 1 for d in dimensions]

        valid_cells = set()
        for x in range(0, grid_size[0], 2):
            for y in range(0, grid_size[1], 2):
                for z in range(0, grid_size[2], 2):
                    world_pos = min_bound + Vector((x * spacing, y * spacing, z * spacing))
                    if point_inside_object(target_obj, world_pos):
                        valid_cells.add((x, y, z))

        maze_cells = generate_maze_cells(valid_cells)

        if settings.use_unified_form:
            build_unified_maze_mesh(maze_cells, spacing, min_bound)
        else:
            build_maze_mesh(maze_cells, spacing, min_bound)

        return {'FINISHED'}

# ------------------------- UI Panel -------------------------

class VIEW3D_PT_maze_maker(bpy.types.Panel):
    bl_label = "MAZE-MAKER TOOL"
    bl_idname = "VIEW3D_PT_maze_maker"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Maze-Maker'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.maze_maker_settings
        cuboid = context.scene.cuboid_maze_props
        meshfill = context.scene.mesh_maze_props

        layout.prop(settings, "use_unified_form", expand=True)
        layout.separator()

        box1 = layout.box()
        box1.label(text="MazGen3D - Generate a cuboid maze", icon='MESH_CUBE')
        box1.prop(cuboid, "maze_x")
        box1.prop(cuboid, "maze_y")
        box1.prop(cuboid, "maze_z")
        box1.prop(cuboid, "seed")
        box1.operator("maze.generate_cuboid_maze", text="Cuboid Maze")

        layout.separator()

        box2 = layout.box()
        box2.label(text="MazFill3D - Fill a mesh object with a maze", icon='MOD_REMESH')
        box2.prop(meshfill, "target_object")
        box2.prop(meshfill, "resolution")
        box2.prop(meshfill, "seed")
        box2.operator("maze.fill_mesh_maze", text="Fill Maze")

# ------------------------- Registration -------------------------

classes = (
    MazeMakerSettings,
    CuboidMazeProperties,
    MeshMazeProperties,
    GenerateCuboidMazeOperator,
    GenerateMeshFillMazeOperator,
    VIEW3D_PT_maze_maker,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.maze_maker_settings = PointerProperty(type=MazeMakerSettings)
    bpy.types.Scene.cuboid_maze_props = PointerProperty(type=CuboidMazeProperties)
    bpy.types.Scene.mesh_maze_props = PointerProperty(type=MeshMazeProperties)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.maze_maker_settings
    del bpy.types.Scene.cuboid_maze_props
    del bpy.types.Scene.mesh_maze_props

if __name__ == "__main__":
    register()
