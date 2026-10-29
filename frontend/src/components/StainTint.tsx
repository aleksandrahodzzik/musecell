import { STAIN_MATRIX } from "../lib/fieldImage";

/** One hidden SVG filter; `.stain-tint` images (composite thumbnails) get the stain colours of the microscope. */
export function StainTintFilter() {
  return (
    <svg aria-hidden width="0" height="0" className="absolute">
      <filter id="stain-tint" colorInterpolationFilters="sRGB">
        <feColorMatrix type="matrix" values={STAIN_MATRIX} />
      </filter>
    </svg>
  );
}
