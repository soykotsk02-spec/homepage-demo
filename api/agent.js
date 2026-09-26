import { createNodeHandler } from '../server/agent-service.mjs';

// All secrets and reports stay server-side. Production storage is Redis REST.
export default createNodeHandler();
