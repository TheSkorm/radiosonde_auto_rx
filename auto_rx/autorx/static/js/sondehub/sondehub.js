var awsIot = require('aws-iot-device-sdk');
var AWS = require('aws-sdk');
AWS.config.region = 'us-east-1';
AWS.config.credentials = new AWS.CognitoIdentityCredentials({
    IdentityPoolId: 'us-east-1:ab2c2cbb-5722-423d-9a2e-efcf278dadee'
 });

 var device = awsIot.device({
    region: 'us-east-1',
    host: 'af1y19ao046nq-ats.iot.us-east-1.amazonaws.com',
    clientId:(Math.floor((Math.random() * 100000) + 1)),
    protocol: 'wss',
    maximumReconnectTimeMs: 8000,
    debug: true,
    accessKeyId: '',
    secretKey: '',
    sessionToken: ''
});

var cognitoIdentity = new AWS.CognitoIdentity();
AWS.config.credentials.get(function(err, data) {
   if (!err) {
      console.log('retrieved identity: ' + AWS.config.credentials.identityId);
      var params = {
         IdentityId: AWS.config.credentials.identityId
      };
      cognitoIdentity.getCredentialsForIdentity(params, function(err, data) {
         if (!err) {
            //
            // Update our latest AWS credentials; the MQTT client will use these
            // during its next reconnect attempt.
            //
            device.updateWebSocketCredentials(data.Credentials.AccessKeyId,
               data.Credentials.SecretKey,
               data.Credentials.SessionToken);
         } else {
            console.log('error retrieving credentials: ' + err);
            alert('error retrieving credentials: ' + err);
         }
      });
   } else {
      console.log('error retrieving identity:' + err);
      alert('error retrieving identity: ' + err);
   }
});

//
// Device is an instance returned by mqtt.Client(), see mqtt.js for full
// documentation.
//
device
  .on('connect', function() {
    console.log('connect');
    device.subscribe('sondes');
  });

device
  .on('message', function(topic, payload) {
    console.log('message', topic, payload.toString());
    packet = JSON.parse(payload.toString())
    raw = packet.data["_raw"]
    telstring = atob(raw)
    telstring = $.trim(telstring)
    data = $.trim(telstring).split(",",10)
    msg = {
      "id": data[0].replace("$$","").replace("RS_",""),
      "frame": data[1],
      "time": data[2],
      "lat": parseFloat(data[3]),
      "lon": parseFloat(data[4]),
      "alt": parseFloat(data[5]),
      "vel_h": parseFloat(data[6]),
      "vel_v": 0,
      "type": "SondeHub",
      "freq": 400, //TODO grab out of comment
      "freq_float": 400,
      "temp": parseFloat(data[7]),
      "humidity": parseFloat(data[8]),
      "comment": data[9].split('*')[0],
      "receivers": Object.keys(packet.receivers),
      "datetime": packet["receivers"][Object.keys(packet.receivers)[0]]["time_created"],
      "time_uploaded": packet["receivers"][Object.keys(packet.receivers)[0]]["time_uploaded"]
    }


    // Telemetry Event messages contain the entire telemetry dictionary, as produced by the SondeDecoder class.
    // This includes the fields: ['frame', 'id', 'datetime', 'lat', 'lon', 'alt', 'temp', 'type', 'freq', 'freq_float']
    // Have we seen this sonde before? 
    if (sonde_positions.hasOwnProperty(msg.id) == false){
        // Nope, add a property to the sonde_positions object, and setup markers for the sonde.
        sonde_positions[msg.id] = {
            latest_data : msg,
            age : Date.now(),
            colour : colour_values[colour_idx]
        };
                                // Create markers
        sonde_positions[msg.id].path = L.polyline([[msg.lat, msg.lon, msg.alt]],{title:msg.id + " Path", color:sonde_positions[msg.id].colour}).addTo(sondemap);

        sonde_positions[msg.id].marker = L.marker([msg.lat, msg.lon, msg.alt],{title:msg.id, icon: sondeAscentIcons[sonde_positions[msg.id].colour]})                            
            .bindTooltip(msg.id,{permanent:false,direction:'right'})
            .addTo(sondemap);

        // If there is a station location defined, show the path from the station to the sonde.
        if(autorx_config.station_lat != 0.0){
            sonde_positions[msg.id].los_path = L.polyline([[autorx_config.station_lat, autorx_config.station_lon],[msg.lat, msg.lon]],
                {
                    color:los_color,
                    opacity:los_opacity
                }
            ).addTo(sondemap);
        }

        colour_idx = (colour_idx+1)%colour_values.length;
        // If this is our first sonde since the browser has been opened, follow it.
        if (Object.keys(sonde_positions).length == 1){
            sonde_positions[msg.id].following = true;
        }
    } else {
        // Yep - update the sonde_positions entry.
        sonde_positions[msg.id].latest_data = msg;
        sonde_positions[msg.id].age = Date.now();
        sonde_positions[msg.id].path.addLatLng([msg.lat, msg.lon, msg.alt]);
        sonde_positions[msg.id].marker.setLatLng([msg.lat, msg.lon, msg.alt]).update();

        if (msg.vel_v < 0){
            sonde_positions[msg.id].marker.setIcon(sondeDescentIcons[sonde_positions[msg.id].colour]);
        }else{
            sonde_positions[msg.id].marker.setIcon(sondeAscentIcons[sonde_positions[msg.id].colour]);
        }

        if(autorx_config.station_lat != 0.0){
            sonde_positions[msg.id].los_path.setLatLngs([[autorx_config.station_lat, autorx_config.station_lon],[msg.lat, msg.lon]]);
        }
    }

    // Update the telemetry table display
    //updateTelemetryText();
    updateTelemetryTable();

    // Are we currently following any other sondes?
    if (sonde_currently_following == "none"){
        // If not, follow this one!
        sonde_currently_following = msg.id;
    }

    // Is sonde following enabled?
    if (document.getElementById("sondeAutoFollow").checked == true){
        // If we are currently following this sonde, snap the map to it.
        if (msg.id == sonde_currently_following){
                sondemap.panTo([msg.lat,msg.lon]);
        }
    }
    console.log(data)
  });