import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import axios from "axios";
import DragDropImageUpload from "./DragDropImageUpload";

vi.mock("axios");

beforeEach(() => {
  vi.resetAllMocks();
  // jsdom doesn't ship URL.createObjectURL — stub it for staged previews.
  if (!global.URL.createObjectURL) {
    global.URL.createObjectURL = vi.fn(() => "blob:fake");
    global.URL.revokeObjectURL = vi.fn();
  }
});

describe("DragDropImageUpload", () => {
  it("renders the dropzone hint", () => {
    render(<DragDropImageUpload />);
    expect(screen.getByText(/drop reference images/i)).toBeDefined();
  });

  it("posts to /upload-refs when subjectId is provided and a file is dropped", async () => {
    const onUploaded = vi.fn();
    axios.post.mockResolvedValueOnce({
      data: {
        subject: { ref_image_paths: ["/data/cast_refs/7/face.png"] },
        saved: ["/data/cast_refs/7/face.png"],
        skipped: [],
      },
    });

    render(
      <DragDropImageUpload subjectId={7} onUploaded={onUploaded} />,
    );

    const dropzone = screen.getByTestId("drag-drop-zone");
    const file = new File(["fake"], "face.png", { type: "image/png" });
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    const [url, formData, opts] = axios.post.mock.calls[0];
    expect(url).toMatch(/\/cast-library\/subjects\/7\/upload-refs$/);
    expect(formData).toBeInstanceOf(FormData);
    expect(opts.headers["Content-Type"]).toBe("multipart/form-data");
    expect(onUploaded).toHaveBeenCalledWith(["/data/cast_refs/7/face.png"]);
  });

  it("stages files locally when no subjectId, then flushTo() posts them", async () => {
    axios.post.mockResolvedValueOnce({
      data: {
        subject: { ref_image_paths: ["/data/cast_refs/42/x.png"] },
        saved: ["/data/cast_refs/42/x.png"],
        skipped: [],
      },
    });

    const ref = React.createRef();
    render(<DragDropImageUpload ref={ref} />);

    const dropzone = screen.getByTestId("drag-drop-zone");
    const file = new File(["fake"], "x.png", { type: "image/png" });
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

    // Nothing posted yet — staging only.
    expect(axios.post).not.toHaveBeenCalled();
    expect(ref.current.hasStagedFiles()).toBe(true);

    await ref.current.flushTo(42);
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post.mock.calls[0][0]).toMatch(/\/subjects\/42\/upload-refs$/);
  });

  it("renders existing paths as chips", () => {
    render(
      <DragDropImageUpload
        subjectId={1}
        existingPaths={["/data/cast_refs/1/headshot.jpg", "/data/cast_refs/1/profile.png"]}
      />,
    );
    expect(screen.getByText("headshot.jpg")).toBeDefined();
    expect(screen.getByText("profile.png")).toBeDefined();
  });
});
