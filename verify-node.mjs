import fs from "node:fs";
import crypto from "node:crypto";

function asciiJsonString(value) {
  return JSON.stringify(value).replace(/[^\x00-\x7f]/g, (character) =>
    `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`
  );
}

function canonical(value) {
  if (value === null || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "string") return asciiJsonString(value);
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new Error("non-interoperable number");
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${asciiJsonString(key)}:${canonical(value[key])}`
    ).join(",")}}`;
  }
  throw new Error(`unsupported type: ${typeof value}`);
}

const path = process.argv[2];
if (!path) throw new Error("usage: node verify-node.mjs RECEIPT.json");
const receipt = JSON.parse(fs.readFileSync(path, "utf8"));
const signature = receipt.signature;
const payloadHash = receipt.payload_hash;
delete receipt.signature;
delete receipt.payload_hash;

const payload = Buffer.from(canonical(receipt), "ascii");
const actualHash = crypto.createHash("sha256").update(payload).digest("hex");
if (actualHash !== payloadHash) throw new Error("payload hash mismatch");
if (signature.algorithm !== "Ed25519") throw new Error("unsupported signature algorithm");

const spkiPrefix = Buffer.from("302a300506032b6570032100", "hex");
const key = crypto.createPublicKey({
  key: Buffer.concat([spkiPrefix, Buffer.from(signature.public_key, "hex")]),
  format: "der",
  type: "spki",
});
const valid = crypto.verify(
  null,
  payload,
  key,
  Buffer.from(signature.value, "hex"),
);
if (!valid) throw new Error("invalid Ed25519 signature");
console.log(`verified ${receipt.kind} ${receipt.payload_hash ?? payloadHash}`);

