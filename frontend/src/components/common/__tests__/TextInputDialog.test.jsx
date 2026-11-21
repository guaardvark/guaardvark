import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import TextInputDialog from "../TextInputDialog";

const renderDialog = (over = {}) => {
  const onSubmit = vi.fn();
  const onClose = vi.fn();
  const utils = render(
    <TextInputDialog
      open
      title="Insert link"
      label="URL"
      onSubmit={onSubmit}
      onClose={onClose}
      {...over}
    />,
  );
  return { onSubmit, onClose, input: screen.getByLabelText(over.label || "URL"), ...utils };
};

describe("TextInputDialog", () => {
  it("submits the trimmed value on Enter", () => {
    const { onSubmit, onClose, input } = renderDialog();
    fireEvent.change(input, { target: { value: "  https://example.com  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith("https://example.com");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("does nothing on Enter or OK while the field is empty", () => {
    const { onSubmit, input } = renderDialog();
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("button", { name: "OK" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("cancels on Escape without submitting", () => {
    const { onSubmit, onClose, input } = renderDialog();
    fireEvent.change(input, { target: { value: "https://example.com" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("prefills the initial value and submits an edit of it", () => {
    const { onSubmit, input } = renderDialog({
      title: "Rename video",
      label: "File name",
      initialValue: "clip.mp4",
      submitLabel: "Rename",
    });
    expect(input).toHaveValue("clip.mp4");
    fireEvent.change(input, { target: { value: "final.mp4" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(onSubmit).toHaveBeenCalledWith("final.mp4");
  });

  it("starts from the initial value again on the next open", () => {
    const { input, rerender } = renderDialog({ initialValue: "one" });
    fireEvent.change(input, { target: { value: "typed" } });
    rerender(<TextInputDialog open={false} title="Insert link" label="URL" initialValue="one" />);
    rerender(<TextInputDialog open title="Insert link" label="URL" initialValue="two" />);
    expect(screen.getByLabelText("URL")).toHaveValue("two");
  });
});
