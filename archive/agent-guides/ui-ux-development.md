# UI and UX Development

Read this guide only when creating, materially changing, or reviewing a user
interface or interaction. It defines reusable quality and accessibility methods,
not a universal visual style.

## Project authority and design direction

- Follow the project's approved users, content, design system, components,
  terminology, visual direction, and supported platforms.
- Palette, typography, layout, motion, and reference products are project
  decisions. Do not impose a universal theme or replace established tokens merely
  to make an interface look different.
- UI quality means clear hierarchy, truthful content, usable interactions, and
  accessible behavior.
- Use `frontend-design` when establishing or materially reshaping design direction.
  Use `web-design-guidelines` as a focused review after reading the affected UI.
- Neither skill overrides requirements or accessibility obligations.

## Accessibility authority and target

- Target WCAG 2.2 Level AA unless a governing policy or product requirement sets
  another level. WCAG 2.2 is backward-compatible with 2.1.
- Use W3C success criteria and current Understanding material as conformance
  authority, not a skill or automated scanner.
- Do not claim conformance without evidence covering the applicable page, states,
  responsive variants, and user flows.
- If current review guidance cannot be fetched, disclose that limitation.

## Semantics and interaction

- Verify semantic controls, accessible names and labels, keyboard navigation,
  visible and unobscured focus, and relevant loading, empty, error, partial-data,
  disabled, and success states.
- Do not rely on color alone to communicate state or meaning.
- Respect reduced-motion preferences and avoid unnecessary motion.
- For applicable WCAG 2.2 patterns, assess focus obscured by sticky or overlay
  content, drag alternatives, 24 by 24 CSS-pixel targets or their permitted spacing
  exception, consistent help, redundant entry, and accessible authentication.
- Prefer touch targets around 44 by 44 CSS pixels when product constraints allow;
  meet applicable minimum requirements and exceptions.
- Do not block password-manager fill, paste, or an equivalent non-cognitive
  authentication method without a valid exception.

## Color and rendered contrast

- Check actual rendered foreground/background combinations rather than trusting
  color names or palette labels.
- Normal text should meet at least 4.5:1 contrast and large text at least 3:1.
- Required non-text indicators and focus indicators should meet at least 3:1,
  subject to the applicable accessibility criteria.

## Responsive behavior and user overrides

- Test 200% text resize, 400% zoom/reflow at an equivalent narrow viewport, and
  WCAG text-spacing overrides.
- Content, controls, focus, and functionality must not be clipped, hidden, or
  overlapped. Avoid two-dimensional page scrolling except where the content's
  meaning genuinely requires it.
- Test representative content at supported viewport sizes in a browser when
  possible. Code review and automated scans do not replace visual and interaction
  verification.

## Typography

- Treat typography as a project-level system rather than a global list of
  fashionable fonts.
- Choose readable, licensed, loadable families and fallbacks appropriate to the
  audience, supported language and script coverage, and visual direction.
- Do not reject a familiar or system font merely because it is common, or select an
  unusual font only to appear distinctive.
- Reuse existing type tokens. Otherwise define a small semantic scale combining
  family, size, weight, line height, and letter spacing using relative units that
  respect user settings.
- Verify the rendered face and available weights and styles. Avoid synthetic bold
  or italic, and do not load another family or weight without visual need and a
  performance justification.
- Prioritize reading: comfortable body text, readable line length and line height,
  meaningful heading hierarchy, and alignment suited to the content.
- Keep long prose left-aligned unless a deliberate validated exception applies.
  Use tabular figures when users compare numeric columns.
- Test localized, long, and mixed-script content rather than treating English copy
  or nominal CSS values as proof of readability.

## Review evidence

- Report findings with file and line references when reviewing code.
- Distinguish code inspection, automated accessibility results, browser checks,
  assistive-technology checks, and complete product acceptance.
- Do not invent metrics, testimonials, content, or accessibility evidence.
