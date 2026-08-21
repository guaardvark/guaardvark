import { describe, it, expect } from "vitest";
import { sanitizeText, stripHtmlTags, detectXSS, detectSuspiciousCode } from "../inputValidation";

describe("stripHtmlTags", () => {
  it("removes tags and survives nested / split tags", () => {
    expect(stripHtmlTags("<b>bold</b> and <script>alert(1)</script >after")).toBe("bold and alert(1)after");
    const split = stripHtmlTags("<<script>script>alert(1)<</script>/script>");
    expect(split).not.toMatch(/<[^>]*>/);
    expect(split).toBe("script>alert(1)/script>");
    expect(stripHtmlTags("a < b")).toBe("a < b");
    expect(stripHtmlTags(null)).toBe("");
  });

  it("is what sanitizeText uses for stripHtml", () => {
    expect(sanitizeText("<i>x</i>y", { stripHtml: true })).toBe("xy");
  });
});

describe("script detection", () => {
  it("flags script blocks whose closing tag carries whitespace", () => {
    for (const sample of ["<script type=\"text/javascript\">x()</script >", "<script>x()</script\t\n bar>"]) {
      expect(detectXSS(sample)).toBe(true);
      expect(detectSuspiciousCode(sample)).toBe(true);
    }
  });
});
