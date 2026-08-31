// Indirection over the product logo: renders whatever component the active
// brand (config/brand.jsx) declares. All UI should render <BrandLogo /> —
// never a concrete logo component — so a downstream brand swaps its logo in
// exactly one place.
import React from "react";
import brand from "../../config/brand";
import { extensionLogo } from "../../extensions";

const BrandLogo = (props) => {
  const Logo = extensionLogo() || brand.logo;
  return <Logo {...props} />;
};

export default BrandLogo;
