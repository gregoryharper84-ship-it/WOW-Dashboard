# Fee-policy regression matrix

Covered cases:
- quadratic multiplier-one baseline
- event override precedence
- six-decimal model-fee rounding
- direct-member balance alignment
- non-direct-member balance alignment
- unknown balance precision fails closed
- zero maker fee for quadratic/no-maker policy
- future series fee change tracking
- stale effective series policy detection
- future event override tracking
- stale event override-clear detection
- active fee waiver fails closed
- unsupported flat fee fails closed
- market/event/series identity mismatch fails closed
- exact series fee-change acquisition with historical rows
- exact event fee-change acquisition
- incomplete event fee-change pagination fails closed
