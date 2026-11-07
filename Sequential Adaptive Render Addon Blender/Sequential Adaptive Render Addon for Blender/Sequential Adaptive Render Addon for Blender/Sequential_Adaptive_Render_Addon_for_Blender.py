import bpy
from bpy.props import StringProperty, IntProperty, PointerProperty, EnumProperty
from bpy.types import Operator, Panel

bl_info = {
    "name": "Adaptive Sequential Image Exporter",
    "blender": (3, 0, 0),
    "category": "3D View",
}

class RENDER_OT_batch_render(Operator):
    bl_idname = "render.batch_render"
    bl_label = "Begin Render Sequence"
    
    def execute(self, context):
        settings = context.scene.batch_render_settings
        main(settings.collection_name, settings.output_folder, settings.pixel_height, settings.light_collection_name, settings.camera, settings.engine)
        return {'FINISHED'}

def render_object(obj, output_path, pixel_height, light_collection_name, camera, engine):
    # Set render engine
    bpy.context.scene.render.engine = engine
    
    # Hide all objects except the target object and those in the light collection
    for o in bpy.data.objects:
        if o != obj and o.name not in bpy.data.collections[light_collection_name].objects:
            o.hide_render = True
    
    # Ensure the object is the only one selected and active
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # Set the selected camera to orthographic
    camera.data.type = 'ORTHO'
    
    # Set the camera aspect ratio based on object dimensions
    bbox = obj.bound_box
    width = max(abs(bbox[0][0] - bbox[4][0]), abs(bbox[1][0] - bbox[5][0]))
    height = max(abs(bbox[0][2] - bbox[1][2]), abs(bbox[4][2] - bbox[5][2]))

    # Skip rendering if width or height is zero
    if width == 0 or height == 0:
        print(f"Skipping {obj.name} due to zero width or height")
        return
    
    aspect_ratio = width / height
    bpy.context.scene.render.resolution_x = int(aspect_ratio * pixel_height)
    bpy.context.scene.render.resolution_y = pixel_height
    
    # Set orthographic scale
    camera.data.ortho_scale = max(width, height)
    
    # Set transparent background
    bpy.context.scene.render.film_transparent = True

    # Adjust camera position and focus
    bpy.ops.view3d.camera_to_view_selected()
    
    # Render the image
    bpy.context.scene.render.filepath = output_path
    bpy.ops.render.render(write_still=True)
    
    # Unhide all objects after rendering
    for o in bpy.data.objects:
        o.hide_render = False

def main(collection_name, output_folder, pixel_height, light_collection_name, camera, engine):
    collection = bpy.data.collections.get(collection_name)
    if not collection:
        print(f"Collection '{collection_name}' not found")
        return
    
    light_collection = bpy.data.collections.get(light_collection_name)
    if not light_collection:
        print(f"Light collection '{light_collection_name}' not found")
        return
    
    for obj in collection.objects:
        if obj.type == 'MESH':
            output_path = f"{output_folder}/{obj.name}.png"
            render_object(obj, output_path, pixel_height, light_collection_name, camera, engine)

class AdaptiveRenderSettings(bpy.types.PropertyGroup):
    collection_name: StringProperty(
        name="Collection",
        description="Collection of objects to render",
        default=""
    )
    
    output_folder: StringProperty(
        name="Output Folder",
        description="Folder to save rendered images",
        default=r"C:\Users\[user]\Desktop\\",  # Using raw string for Windows paths
        subtype='DIR_PATH'
    )
    
    pixel_height: IntProperty(
        name="Pixel Height",
        description="Height of the rendered image in pixels",
        default=1024,
        min=1
    )
    
    light_collection_name: StringProperty(
        name="Light Collection",
        description="Name of the collection containing lights",
        default="Lighting"
    )
    
    camera: PointerProperty(
        name="Camera",
        description="Select the camera to be used for rendering",
        type=bpy.types.Object
    )
    
    engine: EnumProperty(
        name="Render Engine",
        description="Choose render engine",
        items=[
            ('BLENDER_EEVEE', "Eevee", ""),
            ('CYCLES', "Cycles", "")
        ],
        default='BLENDER_EEVEE'
    )

class RENDER_PT_batch_render_panel(Panel):
    bl_label = "Adaptive Render Tool"
    bl_idname = "RENDER_PT_batch_render_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Adaptive Batch Render"
    
    def draw(self, context):
        layout = self.layout
        settings = context.scene.batch_render_settings
        
        layout.prop(settings, "collection_name")  # Define the collection name for the objects to be exported
        layout.prop(settings, "output_folder")  # Define the output directory for your exports
        layout.prop(settings, "pixel_height")  # Define the resolution height, the width will adapt based on this - 1024 by default
        layout.prop(settings, "light_collection_name")  # Define your Lighting Collection so it doesn't get muted during render process
        layout.prop(settings, "camera")  # Eyedropper tool to select camera
        layout.prop(settings, "engine")  # Select between EEVEE and Cycles - eevee by default
        
        layout.operator("render.batch_render")

def register():
    bpy.utils.register_class(RENDER_OT_batch_render)
    bpy.utils.register_class(AdaptiveRenderSettings)
    bpy.utils.register_class(RENDER_PT_batch_render_panel)
    bpy.types.Scene.batch_render_settings = bpy.props.PointerProperty(type=AdaptiveRenderSettings)

def unregister():
    bpy.utils.unregister_class(RENDER_OT_batch_render)
    bpy.utils.unregister_class(AdaptiveRenderSettings)
    bpy.utils.unregister_class(RENDER_PT_batch_render_panel)
    del bpy.types.Scene.batch_render_settings

if __name__ == "__main__":
    register()
