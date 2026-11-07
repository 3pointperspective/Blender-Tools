bl_info = {
    "name": "MP4 Export Tool Addon",
    "blender": (2, 80, 0),
    "category": "3D View",
    "version": (1, 0),
    "author": "Aidan Ault",
    "description": "Custom MP4 exporting panel for animations with options for frame rate, resolution, engine, etc.",
}

import bpy
import os

# Define the properties for the panel
class RenderProperties(bpy.types.PropertyGroup):
    frame_rate: bpy.props.IntProperty(
        name="Frame Rate",
        description="Frames per second",
        default=24,
        min=1,
        max=120
    )
    resolution_x: bpy.props.IntProperty(
        name="Resolution X",
        description="Width of the rendered output",
        default=1920,
        min=1
    )
    resolution_y: bpy.props.IntProperty(
        name="Resolution Y",
        description="Height of the rendered output",
        default=1080,
        min=1
    )
    output_name: bpy.props.StringProperty(
        name="Output File Name",
        description="Name of the output MP4 file",
        default="rendered_animation.mp4"
    )
    output_dir: bpy.props.StringProperty(
        name="Output Directory",
        description="Directory to save the output MP4 file",
        default="C:\\Users\\[user]\\Desktop\\",
        subtype='DIR_PATH'
    )
    render_engine: bpy.props.EnumProperty(
        name="Render Engine",
        description="Choose between EEVEE and Cycles",
        items=[
            ('BLENDER_EEVEE', "EEVEE", "Use EEVEE render engine"),
            ('CYCLES', "Cycles", "Use Cycles render engine")
        ],
        default='BLENDER_EEVEE'
    )

# Define the operator for rendering
class RENDER_OT_CustomRender(bpy.types.Operator):
    bl_idname = "render.custom_render"
    bl_label = "Export Animation"
    bl_description = "Render the animation with the specified settings"

    def execute(self, context):
        props = context.scene.render_properties

        # Set the output directory
        output_dir = props.output_dir
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # Set the output file path
        output_path = os.path.join(output_dir, props.output_name)

        # Set render settings
        bpy.context.scene.render.engine = props.render_engine
        bpy.context.scene.render.resolution_x = props.resolution_x
        bpy.context.scene.render.resolution_y = props.resolution_y
        bpy.context.scene.render.resolution_percentage = 100
        bpy.context.scene.render.fps = props.frame_rate

        bpy.context.scene.render.filepath = output_path
        bpy.context.scene.render.image_settings.file_format = 'FFMPEG'
        bpy.context.scene.render.image_settings.color_mode = 'RGB'
        bpy.context.scene.render.ffmpeg.format = 'MPEG4'
        bpy.context.scene.render.ffmpeg.codec = 'H264'
        bpy.context.scene.render.ffmpeg.constant_rate_factor = 'HIGH'
        bpy.context.scene.render.ffmpeg.ffmpeg_preset = 'GOOD'
        bpy.context.scene.render.ffmpeg.video_bitrate = 6000

        if props.render_engine == 'BLENDER_EEVEE':
            bpy.context.scene.eevee.taa_render_samples = 64  # Anti-aliasing samples

        # Render the animation
        bpy.ops.render.render(animation=True, write_still=True)

        self.report({'INFO'}, f"Render completed and saved to {output_path}")
        return {'FINISHED'}

# Define the panel UI
class RENDER_PT_CustomPanel(bpy.types.Panel):
    bl_label = "MP4 Exporter"
    bl_idname = "RENDER_PT_custom_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MP4 Export Tool'

    def draw(self, context):
        layout = self.layout
        props = context.scene.render_properties

        layout.prop(props, "frame_rate")
        layout.prop(props, "resolution_x")
        layout.prop(props, "resolution_y")
        layout.prop(props, "output_name")
        layout.prop(props, "output_dir")
        layout.prop(props, "render_engine")

        layout.operator("render.custom_render", text="Render Animation")

# Register and unregister functions
classes = [
    RenderProperties,
    RENDER_OT_CustomRender,
    RENDER_PT_CustomPanel
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.render_properties = bpy.props.PointerProperty(type=RenderProperties)

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.render_properties

if __name__ == "__main__":
    register()
