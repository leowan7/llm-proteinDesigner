import "@testing-library/jest-dom/vitest";

// jsdom does not implement scrollIntoView — stub it globally so components
// that call element.scrollIntoView() (e.g. MessageList auto-scroll) do not throw.
Element.prototype.scrollIntoView = () => {};
