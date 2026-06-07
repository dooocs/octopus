type ToggleSwitchProps = {
  checked: boolean
  disabled?: boolean
  label: string
  onChange: (checked: boolean) => void
}

export default function ToggleSwitch({
  checked,
  disabled,
  label,
  onChange
}: ToggleSwitchProps) {
  return (
    <button
      type="button"
      className={`toggle ${checked ? 'toggle-on' : ''}`}
      aria-label={label}
      aria-pressed={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span />
    </button>
  )
}
