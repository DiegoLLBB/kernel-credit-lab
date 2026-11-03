"use client";

import { Component, type ReactNode } from "react";

/**
 * Keeps one failing section from blanking the whole page: the section is replaced by a short
 * note and the rest of the page keeps working.
 */
export class SectionBoundary extends Component<
  { name: string; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(`[Kernel] ${this.props.name} failed to render`, error);
  }

  render() {
    if (this.state.failed) {
      return (
        <p role="status" className="border-t border-memo-rule py-4 text-sm text-memo-muted">
          The {this.props.name} section couldn&apos;t be shown. The rest of the page is unaffected;
          reload to try again.
        </p>
      );
    }
    return this.props.children;
  }
}
