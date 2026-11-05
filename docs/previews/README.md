# Cinematic blue profile review

The before images show the live profile immediately before this change. The after images show the candidate README rendered by GitHub's Markdown API and inserted into the actual GitHub profile layout in an isolated browser, with local candidate assets. They are pre-publication previews, not evidence of deployment.

| View | Before | After |
| --- | --- | --- |
| Desktop, 1440 px, light | [Before](blue-desktop-before.png) | [After](blue-desktop-after.png) |
| Mobile, 390 px, dark | [Before](blue-mobile-before.png) | [After](blue-mobile-after.png) |

Validation used Chrome with ordinary scrollbars at 320, 390, 768 and 1440 px in both color schemes. All images decoded, responsive sources selected correctly, and no horizontal article overflow occurred with all seven disclosures closed or open. All three project disclosures and their source links were keyboard tested. A source link inside a summary navigates without opening the disclosure.

The original pre-redesign baseline was 7,587.20 px tall at 390 px and 4,052.27 px at 1440 px. The new closed profile measures 3,359.91 px on mobile (55.72% shorter, matching 293 px content width) and 2,304.89 px on desktop (approximately 43.12% shorter; content width 846 px versus the original 847 px). The before screenshots here show the immediately preceding implementation, not that earlier baseline.

Generated text bounding boxes fit all 30 SVG variants. A conservative background-pixel contrast check across text bounding boxes measured at least 11.81:1 in the artwork; the palette is shared by the light and dark variants. Native controls retain GitHub's theme styling.

Browser captures of the actual banner picture showed movement with no motion preference, a stable scene after 4.8 seconds, and identical frames throughout with reduced motion enabled. The reduced-motion picture sources select separate assets containing no animation rules. The brain is a rendered illustration with perspective/rotation animation, not a draggable three-dimensional model.

The blue project illustrations are conceptual artwork. The original source previews remain inside project disclosures and retain their documented provenance and visibility checks. See [maintenance](../LIVING_PORTFOLIO.md).
