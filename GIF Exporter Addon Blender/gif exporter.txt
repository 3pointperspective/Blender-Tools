bl_info = {
    "name": "Render Animation as GIF",
    "blender": (3, 0, 0),
    "category": "Render",
}

import bpy
from bpy.props import StringProperty, EnumProperty, IntProperty, PointerProperty
from bpy.types import Operator, Panel, AddonPreferences, PropertyGroup

class RenderGIFProperties(PropertyGroup):
    camera: StringProperty(
        name="Camera",
        description="Camera to use for rendering",
        default=""
    )
    object_collection: StringProperty(
        name="Object Collection",
        description="Collection of objects to include in rendering",
        default=""
    )
    light_collection: StringProperty(
        name="Light Collection",
        description="Collection of lights to include in rendering",
        default=""
    )
    output_directory: StringProperty(
        name="Output Directory",
        description="Directory to save the GIF",
        default="//",
        subtype='DIR_PATH'
    )
    file_name: StringProperty(
        name="File Name",
        description="Name of the output GIF file",
        default="output.gif"
    )
    fps: IntProperty(
        name="Frames per Second",
        description="Frames per second for the animation",
        default=24,
        min=1,
        max=60
    )
    aspect_ratio: EnumProperty(
        name="Aspect Ratio",
        description="Choose aspect ratio for rendering",
        items=[
            ('SQUARE', "Square (540x540)", "Render with a square aspect ratio"),
            ('LANDSCAPE', "Landscape (540x270)", "Render with a landscape aspect ratio"),
            ('PORTRAIT', "Portrait (270x540)", "Render with a portrait aspect ratio"),
        ],
        default='SQUARE'
    )

class RENDER_OT_render_gif(Operator):
    bl_idname = "render.render_gif"
    bl_label = "Render GIF"
    
    def execute(self, context):
        props = context.scene.render_gif_props
        
        # Set the camera
        if props.camera:
            bpy.context.scene.camera = bpy.data.objects.get(props.camera)
        
        # Include only objects from the specified collection
        if props.object_collection:
            obj_collection = bpy.data.collections.get(props.object_collection)
            for obj in bpy.data.objects:
                obj.hide_render = True
            if obj_collection:
                for obj in obj_collection.objects:
                    obj.hide_render = False
        
        # Include only lights from the specified collection
        if props.light_collection:
            light_collection = bpy.data.collections.get(props.light_collection)
            for light in bpy.data.lights:
                light.hide_render = True
            if light_collection:
                for light in light_collection.objects:
                    light.hide_render = False
        
        # Set render resolution based on aspect ratio
        if props.aspect_ratio == 'SQUARE':
            bpy.context.scene.render.resolution_x = 540
            bpy.context.scene.render.resolution_y = 540
        elif props.aspect_ratio == 'LANDSCAPE':
            bpy.context.scene.render.resolution_x = 540
            bpy.context.scene.render.resolution_y = 270
        elif props.aspect_ratio == 'PORTRAIT':
            bpy.context.scene.render.resolution_x = 270
            bpy.context.scene.render.resolution_y = 540

        # Set output settings
        bpy.context.scene.render.image_settings.file_format = 'FFMPEG'
        bpy.context.scene.render.ffmpeg.format = 'MPEG4'
        bpy.context.scene.render.ffmpeg.codec = 'H264'
        bpy.context.scene.render.ffmpeg.constant_rate_factor = 'HIGH'
        bpy.context.scene.render.filepath = f"{props.output_directory}/{props.file_name}"
        bpy.context.scene.render.fps = props.fps

        # Render the animation
        bpy.ops.render.render(animation=True)

        # Convert rendered video to GIF
        output_path = bpy.context.scene.render.filepath
        gif_output_path = f"{output_path}.gif"
        bpy.ops.render.render(animation=True)
        
        # Use ffmpeg to convert the rendered video to GIF
        import os
        command = f"ffmpeg -i {output_path} -vf 'fps={props.fps},scale=540:-1:flags=lanczos' -loop 0 {gif_output_path}"
        os.system(command)

        self.report({'INFO'}, f"GIF saved at {gif_output_path}")
        return {'FINISHED'}

class RENDER_PT_render_gif_panel(Panel):
    bl_label = "Render GIF"
    bl_idname = "RENDER_PT_render_gif_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RenderGIF"

    def draw(self, context):
        layout = self.layout
        props = context.scene.render_gif_props

        layout.prop_search(props, "camera", bpy.data, "cameras", text="Camera")
        layout.prop_search(props, "object_collection", bpy.data, "collections", text="Object Collection")
        layout.prop_search(props, "light_collection", bpy.data, "collections", text="Light Collection")
        layout.prop(props, "output_directory")
        layout.prop(props, "file_name")
        layout.prop(props, "fps")
        layout.prop(props, "aspect_ratio")

        layout.operator("render.render_gif")

def register():
    bpy.utils.register_class(RenderGIFProperties)
    bpy.utils.register_class(RENDER_OT_render_gif)
    bpy.utils.register_class(RENDER_PT_render_gif_panel)
    bpy.types.Scene.render_gif_props = PointerProperty(type=RenderGIFProperties)

def unregister():
    bpy.utils.unregister_class(RenderGIFProperties)
    bpy.utils.unregister_class(RENDER_OT_render_gif)
    bpy.utils.unregister_class(RENDER_PT_render_gif_panel)
    del bpy.types.Scene.render_gif_props

if __name__ == "__main__":
    register()
