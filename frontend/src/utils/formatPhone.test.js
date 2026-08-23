import { describe, it, expect } from "vitest";

import { formatPhone, formatPhoneInput, phoneDigits } from "./formatPhone";

describe("formatPhone", () => {
  it("formats ten digits", () => {
    expect(formatPhone("5551234567")).toBe("(555) 123-4567");
  });

  it("drops a US country code", () => {
    expect(formatPhone("15551234567")).toBe("(555) 123-4567");
    expect(formatPhone("+1 555 123 4567")).toBe("(555) 123-4567");
  });

  it("reformats whatever punctuation was stored", () => {
    expect(formatPhone("555-123-4567")).toBe("(555) 123-4567");
    expect(formatPhone("555.123.4567")).toBe("(555) 123-4567");
    expect(formatPhone("(555)1234567")).toBe("(555) 123-4567");
  });

  it("passes through what it cannot recognise", () => {
    expect(formatPhone("+44 20 7946 0958")).toBe("+44 20 7946 0958");
    expect(formatPhone("555-1234")).toBe("555-1234");
    expect(formatPhone("ext. 400")).toBe("ext. 400");
    expect(formatPhone("call the office")).toBe("call the office");
  });

  it("is empty for empty input", () => {
    expect(formatPhone("")).toBe("");
    expect(formatPhone(null)).toBe("");
    expect(formatPhone(undefined)).toBe("");
  });
});

describe("formatPhoneInput", () => {
  it("grows punctuation as digits arrive", () => {
    expect(formatPhoneInput("5")).toBe("5");
    expect(formatPhoneInput("555")).toBe("555");
    expect(formatPhoneInput("5551")).toBe("(555) 1");
    expect(formatPhoneInput("555123")).toBe("(555) 123");
    expect(formatPhoneInput("5551234")).toBe("(555) 123-4");
    expect(formatPhoneInput("5551234567")).toBe("(555) 123-4567");
  });

  it("stops at ten digits", () => {
    expect(formatPhoneInput("55512345678999")).toBe("(555) 123-4567");
  });

  it("treats a leading 1 as a country code once the number is long enough", () => {
    expect(formatPhoneInput("15551234567")).toBe("(555) 123-4567");
  });

  it("leaves an international number alone", () => {
    expect(formatPhoneInput("+44 20 7946")).toBe("+44 20 7946");
  });

  it("lets the field be cleared", () => {
    expect(formatPhoneInput("")).toBe("");
    expect(formatPhoneInput("()-")).toBe("");
  });
});

describe("phoneDigits", () => {
  it("strips punctuation for tel: links", () => {
    expect(phoneDigits("(555) 123-4567")).toBe("5551234567");
  });

  it("keeps a leading plus", () => {
    expect(phoneDigits("+44 20 7946 0958")).toBe("+442079460958");
  });

  it("is empty for empty input", () => {
    expect(phoneDigits(null)).toBe("");
  });
});
