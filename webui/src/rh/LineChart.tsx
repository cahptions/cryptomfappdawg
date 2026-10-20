import { useMemo, useRef, useState } from "react";

interface Props {
  values: number[];
  labels: string[];
  height?: number;
  onHover?: (index: number | null) => void;
  ariaLabel: string;
}

const WIDTH = 1000;

// Thin line, no axes; green when the period ends above where it started, red otherwise.
// A dashed line marks the starting value. Hover moves a cursor and reports the index.
export default function LineChart({
  values,
  labels,
  height = 240,
  onHover,
  ariaLabel,
}: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useRef<SVGSVGElement | null>(null);
  const geometry = useMemo(() => {
    if (values.length < 2) return null;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const pad = (max - min) * 0.08 || Math.abs(max) * 0.01 || 1;
    const lo = min - pad;
    const hi = max + pad;
    const x = (i: number) => (i / (values.length - 1)) * WIDTH;
    const y = (v: number) => height - ((v - lo) / (hi - lo)) * height;
    const path = values
      .map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(v).toFixed(2)}`)
      .join("");
    return { x, y, path, base: y(values[0]) };
  }, [values, height]);

  if (!geometry) {
    return (
      <div className="rh-chart-empty" style={{ height }}>
        Not enough history for this range yet
      </div>
    );
  }
  const up = values[values.length - 1] >= values[0];

  const move = (clientX: number) => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const index = Math.round(ratio * (values.length - 1));
    setHover(index);
    onHover?.(index);
  };
  const leave = () => {
    setHover(null);
    onHover?.(null);
  };

  return (
    <div className="rh-chart">
      <svg
        ref={box}
        viewBox={`0 0 ${WIDTH} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={ariaLabel}
        className={up ? "is-up" : "is-down"}
        style={{ height }}
        onMouseMove={(e) => move(e.clientX)}
        onMouseLeave={leave}
        onTouchMove={(e) => move(e.touches[0].clientX)}
        onTouchEnd={leave}
      >
        <line
          className="rh-chart-base"
          x1={0}
          x2={WIDTH}
          y1={geometry.base}
          y2={geometry.base}
          vectorEffect="non-scaling-stroke"
        />
        <path
          className="rh-chart-line"
          d={geometry.path}
          vectorEffect="non-scaling-stroke"
        />
        {hover !== null && (
          <>
            <line
              className="rh-chart-cursor"
              x1={geometry.x(hover)}
              x2={geometry.x(hover)}
              y1={0}
              y2={height}
              vectorEffect="non-scaling-stroke"
            />
          </>
        )}
      </svg>
      {hover !== null && (
        <div
          className={`rh-chart-dot ${up ? "is-up" : "is-down"}`}
          style={{
            left: `${(hover / (values.length - 1)) * 100}%`,
            top: `${(geometry.y(values[hover]) / height) * 100}%`,
          }}
        />
      )}
      {hover !== null && (
        <div
          className="rh-chart-label"
          style={{ left: `${(hover / (values.length - 1)) * 100}%` }}
        >
          {labels[hover]}
        </div>
      )}
    </div>
  );
}

export function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <svg className="rh-spark" aria-hidden />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const d = values
    .map(
      (v, i) =>
        `${i ? "L" : "M"}${((i / (values.length - 1)) * 64).toFixed(2)},${(28 - ((v - min) / span) * 26 - 1).toFixed(2)}`,
    )
    .join("");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg
      className={`rh-spark ${up ? "is-up" : "is-down"}`}
      viewBox="0 0 64 28"
      aria-hidden
    >
      <path d={d} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
