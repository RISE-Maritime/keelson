#!/usr/bin/env python3

"""
Command line utility tool for acting as a whep bridge across a zenoh network
"""

# pylint: disable=duplicate-code
# pylint: disable=invalid-name

import sys
import logging
import argparse

import zenoh
import requests
import keelson
from google.protobuf.message import DecodeError
from keelson.interfaces.ErrorResponse_pb2 import ErrorResponse
from keelson.interfaces.WHEPProxy_pb2 import WHEPRequest, WHEPResponse
from keelson.scaffolding import (
    setup_logging,
    add_common_arguments,
    create_zenoh_config,
    suppress_exception,
)


def whep(session: zenoh.Session, args: argparse.Namespace):
    """
    See here for details: https://github.com/bluenviron/mediamtx?tab=readme-ov-file#webrtc
    """

    key = keelson.construct_rpc_key(
        base_path=args.realm,
        entity_id=args.entity_id,
        procedure="whep_signal",
        responder_id=args.responder_id,
    )

    logging.info("Declaring queryable on key: %s", key)
    queryable = session.declare_queryable(key, complete=True)

    while True:
        query: zenoh.Query
        with (
            suppress_exception(Exception, context="WHEP callback"),
            queryable.recv() as query,
        ):

            if query.payload is None:
                message = (
                    "Missing a payload in the query. It should be of type WHEPRequest"
                )
                logging.error(message)
                query.reply_err(ErrorResponse(message).SerializeToString())
                return

            try:
                body = WHEPRequest.FromString(query.payload.to_bytes())
            except DecodeError as exc:
                message = f"Failed to parse the body as a WHEPRequest: {exc}"
                logging.exception(message)
                query.reply_err(ErrorResponse(message).SerializeToString())
                return

            # Build full http url for the resource
            url = f"{args.whep_host}/{body.path}/whep"
            logging.debug("Full http url: %s", url)

            try:
                res = requests.post(
                    url,
                    headers={"Content-Type": "application/sdp"},
                    data=body.sdp,
                    timeout=args.timeout,
                )
                res.raise_for_status()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                message = f"WHEP request failed with reason: {exc}"
                logging.exception(message)
                query.reply_err(ErrorResponse(message).SerializeToString())
                return

            # Success, return response sdp
            logging.debug(
                "Successful WHEP request, returning response SDP: %s", res.text
            )
            query.reply(query.key_expr, WHEPResponse(res.text).SerializeToString())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="mediamtx",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_arguments(parser)

    parser.add_argument("-r", "--realm", type=str, required=True)
    parser.add_argument("-e", "--entity-id", type=str, required=True)

    # Subcommands
    subparsers = parser.add_subparsers(required=True)

    # whep subcommand
    whep_parser = subparsers.add_parser("whep")
    whep_parser.set_defaults(func=whep)
    whep_parser.add_argument("--whep-host", type=str, required=True)
    whep_parser.add_argument("-i", "--responder-id", type=str, required=True)
    whep_parser.add_argument("-t", "--timeout", type=int, default=5, required=False)

    # Parse arguments and start doing our thing
    args = parser.parse_args()

    # Setup logger
    setup_logging(level=args.log_level)

    # Construct session
    logging.info("Opening Zenoh session...")
    conf = create_zenoh_config(
        mode=args.mode,
        connect=args.connect,
        listen=args.listen,
    )

    with zenoh.open(conf) as session:
        # Dispatch to correct function
        try:
            args.func(session, args)
        except KeyboardInterrupt:
            logging.info("Closing down on user request!")
            sys.exit(0)
