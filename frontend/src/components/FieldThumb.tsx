// A field composite thumbnail. Recorded demo runs carry only some of their fields (spec: ≤ 40 PNGs in demo/),
// so a missing image becomes a labelled dark box instead of a broken-image icon.

import { useState } from "react";
import { imageUrl } from "../lib/api";

interface Props {
  imageId: string;
  alt: string;
  width: number;
  height: number;
  className: string;
}

export default function FieldThumb({ imageId, alt, width, height, className }: Props) {
  const [missing, setMissing] = useState(false);
  if (missing) {
    return (
      <span role="img" aria-label={`${alt} (image not in this recording)`} title="Image not in this recording"
            style={{ width, height }} className="bg-darkfield on-dark flex items-center justify-center text-[10px] text-white/60">
        not in demo
      </span>
    );
  }
  return <img src={imageUrl(imageId)} alt={alt} loading="lazy" width={width} height={height} className={className}
              onError={() => setMissing(true)} />;
}
