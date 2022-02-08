from typing import Any, Dict

from synapse.module_api import EventBase, ModuleApi

from synapse.types import (
    create_requester,
    UserID,
    UserInfo,
    JsonDict,
    RoomAlias,
    RoomID,
)
from synapse.api.constants import (
    EventContentFields,
    EventTypes,
    HistoryVisibility,
    JoinRules,
    Membership,
    RoomTypes,
)

from typing import (
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
    overload,
)

from synapse.http.servlet import (
    ResolveRoomIdMixin,
    RestServlet,
    assert_params_in_dict,
    parse_boolean,
    parse_integer,
    parse_json_object_from_request,
    parse_string,
    parse_strings_from_args,
)

import json

import logging
logger = logging.getLogger(__name__)

import requests

import traceback

class KickSpaceRooms:
    def __init__(self, config: Dict[str, Any], api: ModuleApi):
        # Keep a reference to the Module API.
        self._api = api
        self._homeserver = api._hs 
        self._room_member_handler = self._homeserver.get_room_member_handler()
        self._server_name  = self._homeserver.config.server.server_name
        self._store = self._homeserver.get_datastore()


        self._api.register_third_party_rules_callbacks(
            on_new_event=self.on_leave_event,
        )

    async def is_room_a_space(self,event: EventBase):
        if "room_id" not in event :
            return False;
        room_id = event.room_id
        room_entry = await self._store.get_room_with_stats(room_id) 
        logger.info(room_entry.keys())
        logger.info(room_entry.values())
        if room_entry == None:
            return False

        current_state_ids = await self._store.get_current_state_ids(room_id)
        create_event = await self._store.get_event(
            current_state_ids[(EventTypes.Create, "")]
        )


        if create_event.content.get(EventContentFields.ROOM_TYPE) == RoomTypes.SPACE :
            return True
        return False

    def get_event_information(self, event : EventBase): 
        values = dict()
        values['room_id'] = event.room_id

        for entry in event.unsigned['invite_room_state']:
            logger.info(entry)
            if 'type' not in entry :
                continue
            if entry['type'] == 'm.room.create':
                    values['is_space'] = ('type' in entry['content'] and entry['content']['type'] == 'm.space')
        return values['room_id']

    async def on_leave_event(self, event: EventBase, *args: Any) -> None:
        """Listens for new events, and if the event is an invite for a local user then
        automatically accepts it.

        Args:
            event: The incoming event.
        """
        event_dict = event.get_dict()
        logger.info(event_dict)
        is_space = await self.is_room_a_space(event)
        room_id = self.get_event_information(event) 
        # Check if the event is an invite for a local user.
        if (
            event.type == "m.room.member"
            and event.is_state()
            and event.membership == "leave"
            and self._api.is_mine(event.state_key)
            and is_space == True
        ):
            logger.info("Event.type = %s,event.state_key=%s,event.room_id=%s",event.type,event.state_key,event.room_id)
            requester = create_requester('@admin:'+self._server_name, "syt_YWRtaW4_LQSDuXTmsrLjeegTeohm_3MPJch")
            admin = UserID.from_string('@admin:'+self._server_name)
            admin_requester = create_requester(
                admin, authenticated_entity=requester.authenticated_entity
            )

            try:
                # https://github.com/matrix-org/synapse/blob/develop/synapse/handlers/room_summary.py#L257
                room_summary_handler =self._homeserver.get_room_summary_handler()
                logger.info("Request hierarchy for room_id =%s",room_id)
                rooms = await room_summary_handler.get_room_hierarchy(
                    admin_requester,
                    room_id,
                    suggested_only=False,
                    max_depth=1,
                    limit=None,
                )
                #wenn keine rooms da, dann falsche Zugriff oder es gibt keine, sollte aber nicht möglich sein!
                if 'rooms' not in rooms:
                    logger.info('NO ROOMS')
                    return None

                room_ids = await self._store.get_rooms_for_user(event.state_key)
                user_room_list = list(room_ids)
                for room in rooms['rooms'] :
                    if 'room_type' in room and room['room_type'] == 'm.space':
                        continue

                    #is_in_room = await self._store.is_host_joined(room['room_id'], self._server_name )

                    if room['room_id'] in user_room_list:
                        continue;

                    logger.info("Leave RoomiD = %s, roomName = %s",room['room_id'],room['name'])
                    l_room_id, l_remote_room_hosts = await self.resolve_room_id(room['room_id'])


                    # Make the user join the room.
                    await self._api.update_room_membership(
                        sender=admin_requester.user,
                        target=event.state_key,
                        room_id=l_room_id,
                        new_membership="leave",
                    )
            except Exception as e:
                logger.info(traceback.format_exc())
                return None;

    async def resolve_room_id(
        self, room_identifier: str, remote_room_hosts: Optional[List[str]] = None
    ) -> Tuple[str, Optional[List[str]]]:
        """
        from synapse/rest/servlet.py
        Resolve a room identifier to a room ID, if necessary.

        This also performanes checks to ensure the room ID is of the proper form.

        Args:
            room_identifier: The room ID or alias.
            remote_room_hosts: The potential remote room hosts to use.

        Returns:
            The resolved room ID.

        Raises:
            SynapseError if the room ID is of the wrong form.
        """
        if RoomID.is_valid(room_identifier):
            resolved_room_id = room_identifier
        elif RoomAlias.is_valid(room_identifier):
            room_alias = RoomAlias.from_string(room_identifier)
            (
                room_id,
                remote_room_hosts,
            ) = await self._room_member_handler.lookup_room_alias(room_alias)
            resolved_room_id = room_id.to_string()
        else:
            raise Exception(
                400, "%s was not legal room ID or room alias" % (room_identifier,)
            )
        if not resolved_room_id:
            raise Exception(
                400, "Unknown room ID or room alias %s" % room_identifier
            )
        return resolved_room_id, remote_room_hosts

