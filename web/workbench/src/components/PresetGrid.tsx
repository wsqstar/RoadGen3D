import type { ScenePreset } from "../lib/types";

interface PresetGridProps {
  selectedPreset: ScenePreset | null;
  onSelect: (preset: ScenePreset) => void;
  presets: ScenePreset[];
}

export function PresetGrid({ selectedPreset, onSelect, presets }: PresetGridProps) {
  return (
    <div className="preset-grid">
      {presets.map((preset) => {
        const isSelected = selectedPreset?.id === preset.id;
        return (
          <div
            key={preset.id}
            className={`preset-card ${isSelected ? "selected" : ""}`}
            onClick={() => onSelect(preset)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") onSelect(preset);
            }}
          >
            <div
              className="preset-icon"
              style={{
                backgroundColor: `${preset.color}20`,
                color: preset.color,
              }}
            >
              {preset.icon}
            </div>
            <div className="preset-name">{preset.name}</div>
            <div className="preset-name-en">{preset.nameEn}</div>
            <div className="preset-desc">{preset.description}</div>
          </div>
        );
      })}
    </div>
  );
}
