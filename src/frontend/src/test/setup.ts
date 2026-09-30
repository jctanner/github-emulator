import "@testing-library/jest-dom/vitest";

import {cleanup} from "@testing-library/react";
import {afterEach} from "vitest";

// Globals are off, so Testing Library does not unmount between tests itself.
afterEach(cleanup);
