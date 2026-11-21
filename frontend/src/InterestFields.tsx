export const interestOptions = [
  'Stock markets', 'Technology', 'Central banks and inflation',
  'Currencies', 'Energy and commodities', 'Company earnings',
  'Artificial intelligence', 'Climate and clean energy', 'Science and space',
  'Health and biotech', 'Housing and real estate', 'Jobs and the economy',
  'Personal finance', 'Startups and entrepreneurship', 'Global trade',
  'Politics and geopolitics', 'Food and agriculture', 'Transport and mobility',
  'Media and entertainment', 'Sports business',
]

type Props = {
  topics?: string[]
  otherInterests?: string
  onTopicToggle?: (topic: string) => void
  onOtherChange?: (value: string) => void
}

export function InterestFields({ topics, otherInterests, onTopicToggle, onOtherChange }: Props) {
  return <fieldset className="interest-options">
    <legend className="mb-2 text-sm font-medium">Which topics interest you?</legend>
    {interestOptions.map(interest => (
      <label key={interest} className="interest-choice">
        <input type="checkbox" name="interests" value={interest} checked={topics?.includes(interest)}
          onChange={onTopicToggle && (() => onTopicToggle(interest))} />
        {interest}
      </label>
    ))}
    <label className="block text-sm font-medium">
      Other interests (optional)
      <input name="other_interests" type="text" maxLength={300} className="form-field" value={otherInterests}
        onChange={onOtherChange && (event => onOtherChange(event.target.value))} />
    </label>
  </fieldset>
}
