# -*- coding: utf-8 -*-
# The MIT License (MIT)
#
# Copyright (c) 2021 OpenALTO Community
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Authors:
# - Jensen Zhang <jingxuan.n.zhang@gmail.com>
# - Kai Gao <emiapwil@gmail.com>

from .db import data_broker_manager


class IRDService:
    """
    Backend algorithm for IRD generation.
    """

    def __init__(self, namespace, **kwargs):
        self.ns = namespace

    def list_resources(self):
        pass


class EndpointPropertyService:
    """
    Backend algorithm for EPS.
    """

    def __init__(self, namespace, autoreload=True, **kwargs):
        self.ns = namespace
        self.autoreload = autoreload
        self.eb = data_broker_manager.get(self.ns, db_type='endpoint')

    def lookup(self, endpoints, prop_names):
        property_map = dict()
        if self.autoreload:
            self.eb.build_cache()
        for endpoint in endpoints:
            property_map[endpoint] = self.eb.lookup(endpoint, prop_names)
        return property_map


class GeoIPPropertyService:
    """
    Backend algorithm for entity property using GeoIP database.
    """

    def __init__(self, namespace, data_source=None, autoreload=True, **kwargs):
        self.ns = namespace
        self.autoreload = autoreload
        self.db = data_broker_manager.get(self.ns, db_type='delegate')
        self.data_source = data_source

    def lookup(self, entities):
        if self.autoreload:
            self.db.build_cache()

        endpoints = dict()
        for e in entities:
            domain, entityid = e.split(':', 1)
            if domain not in endpoints:
                endpoints[domain] = []
            endpoints[domain].append(entityid)
        geomap = dict()
        for domain in endpoints.keys():
            geomap[domain] = self.db.lookup(self.data_source, endpoints[domain])
        property_map = dict()
        for domain in geomap.keys():
            for entityid, geoinfo in geomap[domain].items():
                endpoint = '{}:{}'.format(domain, entityid)
                property_map[endpoint] = dict()
                property_map[endpoint]['geolocation'] = {"lat": geoinfo[0],
                                                         "lng": geoinfo[1]}
        return property_map


class PathVectorService:
    """
    Backend algorithm for ECS with path vector extension
    """

    def __init__(self, namespace, autoreload=True, **kwargs) -> None:
        self.ns = namespace
        self.autoreload = autoreload
        self.fib = data_broker_manager.get(self.ns, db_type='forwarding')
        self.eb = data_broker_manager.get(self.ns, db_type='endpoint')

    def parse_flow(self, flow):
        """
        Extract attributes of a flow object.

        Parameters
        ----------
        flow : object

        Return
        ------
        A tuple of attributes.
        """
        return flow[0], flow[0], flow[1]

    def iterate_next_hops(self, ingress, dst, ane_dict, property_map, ane_path):
        ingress_prop = self.eb.lookup(ingress, ['dpid', 'in_port'])
        dpid = ingress_prop.get('dpid')
        if not dpid:
            return None, ane_path
        in_port = ingress_prop.get('in_port')
        if not in_port:
            in_port = '0'

        action = self.fib.lookup(dpid, dst, in_port=in_port)
        nh = action.next_hop
        if not nh:
            # last hop, exit
            return action, ane_path
        outgoing_link = action.actions.get('outgoing_link')
        if outgoing_link:
            ane_name = outgoing_link
            if ane_name not in property_map:
                nh_props = self.eb.lookup(nh, property_names=['incoming_links'])
                incoming_links = nh_props.get('incoming_links', dict())
                property_map[ane_name] = incoming_links.get(ane_name, dict())
        else:
            nh_ane = (dpid, nh)
            if nh_ane not in ane_dict:
                ane_idx = len(ane_dict) + 1
                ane_name = 'autolink_{}'.format(ane_idx)
                ane_dict[nh_ane] = ane_name
            ane_name = ane_dict[nh_ane]
            if ane_name not in property_map:
                property_map[ane_name] = dict()
        property_map[ane_name]['next_hop'] = nh
        if ane_name in ane_path:
            # find loop, exit
            return action, ane_path
        ane_path.append(ane_name)
        return self.iterate_next_hops(nh, dst, ane_dict, property_map, ane_path)

    def lookup(self, flows, property_names):
        """
        Parameters
        ----------
        flows : list
            A list of flow objects.

        Returns
        -------
        paths : list
            A list of ane paths.
        propery_map : dict
            Mapping from ane to properties.
        """
        if self.autoreload:
            self.fib.build_cache()
            self.eb.build_cache()

        paths = dict()
        property_map = dict()

        ane_dict = dict()
        as_path_dict = dict()

        for flow in flows:
            ingress, src, dst = self.parse_flow(flow)
            src_prop = self.eb.lookup(src)
            if src_prop is None:
                continue
            if not src_prop.get('is_local'):
                continue

            if src not in paths:
                paths[src] = dict()

            ane_path = list()
            last_action, ane_path = self.iterate_next_hops(ingress, dst, ane_dict, property_map, ane_path)

            as_path = ''
            if last_action:
                as_path = ' '.join(last_action.actions.get('as_path', [])[:-1])
            if len(as_path) > 0:
                if as_path not in as_path_dict:
                    as_path_idx = len(as_path_dict) + 1
                    as_path_ane = 'autopath_{}'.format(as_path_idx)
                    as_path_dict[as_path] = as_path_ane
                    property_map[as_path_ane] = dict()
                    if property_names is not None and 'as_path' in property_names:
                        property_map[as_path_ane]['as_path'] = as_path
                as_path_ane = as_path_dict[as_path]
                ane_path.append(as_path_ane)

            paths[src][dst] = ane_path

        property_map = {ane: {pname: pval
                              for pname, pval in props.items() if pname in property_names}
                        for ane, props in property_map.items()}

        return paths, property_map

