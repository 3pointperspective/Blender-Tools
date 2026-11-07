import os
import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
from PIL import Image, ImageSequence


def select_input_folder():
    """Select the input folder containing PNG images."""
    folder = filedialog.askdirectory(title="Select Input Folder with PNG Images")
    return folder


def select_output_folder():
    """Select the output folder for the GIF file."""
    folder = filedialog.askdirectory(title="Select Output Folder for GIF")
    return folder


def resize_image(image, width, height):
    """Resize image using anti-aliasing filter for better quality."""
    return image.resize((width, height), Image.LANCZOS)


def estimate_gif_size(num_frames, width, height, colors=256):
    """Estimate GIF size in bytes."""
    return num_frames * width * height * (colors / 256) * 3 // 2


def create_gif(input_folder, output_folder, output_name, frame_duration, target_size_mb):
    try:
        # Ensure output folder exists
        os.makedirs(output_folder, exist_ok=True)

        # Output file path
        output_path = os.path.join(output_folder, f"{output_name}.gif")

        # Load images
        images = sorted(
            [f for f in os.listdir(input_folder) if f.endswith('.png')],
            key=lambda x: int(''.join(filter(str.isdigit, x)))  # Sort numerically
        )

        if not images:
            messagebox.showerror("Error", "No PNG images found in the selected folder.")
            return

        # Load the first image to get initial size
        first_image = Image.open(os.path.join(input_folder, images[0]))
        width, height = first_image.size

        # Calculate target size in bytes
        target_size_bytes = target_size_mb * 1024 * 1024

        # Estimate number of frames
        num_frames = len(images)

        # Adjust resolution dynamically to meet target size
        while estimate_gif_size(num_frames, width, height) > target_size_bytes:
            width = int(width * 0.9)
            height = int(height * 0.9)

        # Process frames with color quantization and dithering
        frames = []
        for image_name in images:
            img_path = os.path.join(input_folder, image_name)
            img = Image.open(img_path)

            # Resize while maintaining quality
            img = resize_image(img, width, height)

            # Optimize colors (quantize) and enable dithering
            img = img.convert('P', palette=Image.ADAPTIVE, colors=256)
            frames.append(img)

        # Save as optimized GIF
        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=frame_duration,
            loop=0,
            optimize=True,  # Enable built-in optimization
            disposal=2  # Replace each frame instead of stacking
        )

        messagebox.showinfo("Success", f"GIF saved to {output_path}")

    except Exception as e:
        messagebox.showerror("Error", str(e))


def main():
    # Create GUI window
    root = tk.Tk()
    root.withdraw()  # Hide the root window

    # Select input folder
    input_folder = select_input_folder()
    if not input_folder:
        return

    # Select output folder
    output_folder = select_output_folder()
    if not output_folder:
        return

    # Ask for output file name
    output_name = simpledialog.askstring("Output Name", "Enter name for output GIF file (without extension):")
    if not output_name:
        return

    # Ask for frame duration (milliseconds)
    frame_duration = simpledialog.askinteger("Frame Duration", "Enter frame duration (ms per frame):", minvalue=1)
    if not frame_duration:
        return

    # Ask for target file size in MB
    target_size_mb = simpledialog.askfloat("Target Size", "Enter target file size (MB):", minvalue=0.1)
    if not target_size_mb:
        return

    # Create the GIF
    create_gif(input_folder, output_folder, output_name, frame_duration, target_size_mb)


if __name__ == "__main__":
    main()
