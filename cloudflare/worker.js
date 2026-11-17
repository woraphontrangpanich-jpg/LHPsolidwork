// Cloudflare Worker in front of the SheetFlat container (Cloudflare Containers, Workers Paid plan).
// Every request goes to ONE named container instance, because jobs are kept in that instance's memory.
import { Container, getContainer } from "@cloudflare/containers";

export class SheetFlat extends Container {
  defaultPort = 8080;
  sleepAfter = "30m"; // stop the container after 30 idle minutes (open jobs are then lost)
  envVars = { SHEETFLAT_CLOUD: "1", SHEETFLAT_MAX_UPLOAD_MB: "100" };
}

export default {
  async fetch(request, env) {
    return getContainer(env.SHEETFLAT, "main").fetch(request);
  },
};
