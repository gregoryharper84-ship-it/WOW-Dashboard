# Current documentation precision check

During this implementation, the current Kalshi fixed-point fee-rounding documentation was rechecked and found to supersede the earlier centicent-only model-fee assumption: model trade fees are rounded upward to six decimal dollar places, while account balance alignment is a separate step using the member's target balance precision.

This correction was applied before protected CI and before any fee-policy certification.
