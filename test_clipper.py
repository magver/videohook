import json
import subprocess
from pathlib import Path
from clipper import render_vertical_clip, get_ffmpeg_path


def get_media_info(file_path: str, ffmpeg_bin: str) -> dict:
    """Extract stream details using ffmpeg probe."""
    ffprobe_bin = str(Path(ffmpeg_bin).parent / "ffprobe.exe")
    # If ffprobe doesn't exist next to ffmpeg, fallback to ffmpeg -i info parsing
    cmd = [
        ffmpeg_bin,
        "-i", file_path,
        "-hide_banner"
    ]
    res = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True, errors="replace")
    return {"stderr": res.stderr}


def generate_sample_video(output_path: str, ffmpeg_bin: str, duration: float = 6.0):
    """Generates a synthetic 16:9 test video with audio."""
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size=1280x720:rate=30",
        "-f", "lavfi",
        "-i", f"sine=frequency=1000:duration={duration}",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        output_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main():
    ffmpeg_bin = get_ffmpeg_path()
    print(f"Using FFmpeg: {ffmpeg_bin}")

    sample_input = "test_sample_16_9.mp4"
    sample_output = "test_output_9_16.mp4"

    print("Generating sample 16:9 video...")
    generate_sample_video(sample_input, ffmpeg_bin, duration=6.0)
    print(f"Sample generated: {sample_input} ({Path(sample_input).stat().st_size} bytes)")

    print("Running render_vertical_clip...")
    out = render_vertical_clip(
        input_path=sample_input,
        output_path=sample_output,
        start_time=1.0,
        duration=3.0,
        watermark_text="Тест Водяного Знака",
        speed_factor=1.03
    )

    out_p = Path(out)
    assert out_p.exists(), "Output file does not exist!"
    assert out_p.stat().st_size > 0, "Output file is empty!"
    print(f"Successfully created: {out} ({out_p.stat().st_size} bytes)")

    info = get_media_info(out, ffmpeg_bin)
    stderr = info["stderr"]
    print("\n--- Media Info Verification ---")
    assert "1080x1920" in stderr, "Resolution 1080x1920 not found in output stream!"
    assert "yuv420p" in stderr, "yuv420p not found in output stream!"
    assert "Audio: aac" in stderr, "Audio aac not found in output stream!"
    print("Verification passed! 1080x1920, yuv420p, AAC audio confirmed.")

    # Cleanup temporary test files
    try:
        Path(sample_input).unlink(missing_ok=True)
        Path(sample_output).unlink(missing_ok=True)
        print("Cleaned up temporary test files.")
    except Exception as e:
        print(f"Cleanup note: {e}")


if __name__ == "__main__":
    main()
