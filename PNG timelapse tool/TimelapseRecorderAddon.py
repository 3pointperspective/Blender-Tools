bl_info = {
    "name": "Timelapse Screenshot Addon",
    "blender": (2, 80, 0),
    "category": "3D View",
    "version": (1, 0, 2),
    "author": "Scripting by Aidan Ault, bug fixes by Hunter Ault",
    "terms and conditions": "Please do not replicate or resell this script/addon. Intended for personal and professional use in Blender.",
    "description": "Automatically take incremental screenshots of the active view at configurable intervals, with definable scale and resolution."
}

import bpy
import os


class ScreenshotSettings(bpy.types.PropertyGroup):
    """Stores settings for the screenshot addon"""
    is_running: bpy.props.BoolProperty(
        name="ACTIVATE",
        description="Toggle screenshot capturing",
        default=False,
        update=lambda self, context: bpy.ops.view3d.screenshot_operator('INVOKE_DEFAULT')
    )

    output_path: bpy.props.StringProperty(
        name="Output Path",
        description="Screenshot Output Directory",
        default="//screenshots/",
        subtype='DIR_PATH'
    )

    resolution_x: bpy.props.IntProperty(
        name="Width",
        description="Screenshot Width",
        default=1920,
        min=1
    )

    resolution_y: bpy.props.IntProperty(
        name="Height",
        description="Screenshot Height",
        default=1080,
        min=1
    )

    delay: bpy.props.FloatProperty(
        name="Delay (seconds)",
        description="Timelapse Capture Delay",
        default=20.0,
        min=1.0
    )

    dpi: bpy.props.IntProperty(
        name="Resolution Scale (%)",
        description="Resolution scaling percentage",
        default=72,
        min=1
    )

    filename_prefix: bpy.props.StringProperty(
        name="Filename Prefix",
        description="Prefix for screenshot filenames",
        default="name_here"
    )

    sequence_number: bpy.props.IntProperty(
        name="Sequence Start",
        description="Starting number for screenshot filenames",
        default=1,
        min=1
    )


class ScreenshotOperator(bpy.types.Operator):
    """Operator to handle screenshot capturing"""
    bl_idname = "view3d.screenshot_operator"
    bl_label = "Screenshot Operator"

    _timer = None

    def modal(self, context, event):
        if not context.scene.screenshot_settings.is_running:
            self.cancel(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            self.take_screenshot(context)

        return {'PASS_THROUGH'}

    def execute(self, context):
        settings = context.scene.screenshot_settings

        if settings.is_running:
            # Start the timer
            self._timer = context.window_manager.event_timer_add(settings.delay, window=context.window)
            context.window_manager.modal_handler_add(self)
            self.report({'INFO'}, "Starting automatic screenshots...")
        else:
            self.report({'INFO'}, "Stopping automatic screenshots...")
            self.cancel(context)

        return {'RUNNING_MODAL'}

    def take_screenshot(self, context):
        settings = context.scene.screenshot_settings

        # Ensure the output directory exists
        output_path = bpy.path.abspath(settings.output_path)
        os.makedirs(output_path, exist_ok=True)

        # Generate the screenshot filename
        filename = f"{settings.filename_prefix}_{settings.sequence_number:06d}.png"
        filepath = os.path.join(output_path, filename)

        # Set resolution and capture the screenshot
        bpy.context.scene.render.resolution_x = settings.resolution_x
        bpy.context.scene.render.resolution_y = settings.resolution_y
        bpy.context.scene.render.resolution_percentage = settings.dpi
        bpy.ops.screen.screenshot(filepath=filepath)

        # Increment the sequence number
        settings.sequence_number += 1

    def cancel(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None


class ScreenshotPanel(bpy.types.Panel):
    """UI Panel to control the screenshot addon"""
    bl_label = "Incremental Screenshots"
    bl_idname = "VIEW3D_PT_screenshot_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Screenshots'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.screenshot_settings

        layout.prop(settings, "is_running", toggle=True)
        layout.prop(settings, "output_path")
        layout.prop(settings, "resolution_x")
        layout.prop(settings, "resolution_y")
        layout.prop(settings, "dpi")
        layout.prop(settings, "delay")
        layout.prop(settings, "filename_prefix")
        layout.prop(settings, "sequence_number")


classes = [
    ScreenshotSettings,
    ScreenshotOperator,
    ScreenshotPanel
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.screenshot_settings = bpy.props.PointerProperty(type=ScreenshotSettings)


def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.screenshot_settings


if __name__ == "__main__":
    register()
